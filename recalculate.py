"""以公開摘要資料同步更新班表、薪資、退稅及儲蓄；只使用 Python 標準函式庫。"""
import json
import re
from collections import OrderedDict
from datetime import date, timedelta
from decimal import Decimal as D, ROUND_HALF_UP
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent
data = json.loads((ROOT / 'forecast-data.json').read_text())
day = date.fromisoformat
fmt = lambda value, places=0: f'{D(value).quantize(D(1).scaleb(-places), rounding=ROUND_HALF_UP):,.{places}f}'
md = lambda value: f'{value.month}/{value.day}'
span = lambda a, b: md(a) + ('–' + md(b) if a != b else '')
weekday = lambda value: '一二三四五六日'[value.weekday()]
number = lambda n: {1:'一',2:'二',3:'三',4:'四',5:'五',6:'六',7:'七',8:'八',9:'九',10:'十'}.get(n, str(n))
asof = day(data['updated'])
fx = D(data['fx']['buy_rate'])
rate = D(data['hourly_rate']) * (1 + D(data['holiday_rate']))
cost = sum(D(v) for v in data['weekly_costs'].values())
cycle = data['pay_cycle_days']
frequency = D(364) / cycle
tax_start, tax_end = day(data['tax_year_start']), day(data['tax_year_end'])


def income_tax(amount):
    bounds = [(D(15600), D('.105')), (D(53500), D('.175')), (D(78100), D('.30')), (D(180000), D('.33'))]
    total, lower = D(0), D(0)
    for upper, percentage in bounds:
        total += max(D(0), min(amount, upper) - lower) * percentage
        lower = upper
    return total + max(D(0), amount - lower) * D('.39')


def acc(amount):
    return min(amount, D(data['acc_cap'])) * D(data['acc_rate'])


def payment(hours):
    gross = hours * rate
    paye = (income_tax(gross * frequency) + acc(gross * frequency)) / frequency
    return {'hours': hours, 'gross': gross, 'paye': paye, 'net': gross - paye}


weeks = data['weeks']
by_number = {w['number']: w for w in weeks}
scheduled, paid, shift_hours = {}, {}, {}
for week in weeks:
    total = D(0)
    for shift in week['shifts']:
        if not day(week['start']) <= day(shift['date']) <= day(week['start']) + timedelta(days=6):
            raise ValueError('班次日期不在指定週內')
        start, end = [sum(int(n) * unit for n, unit in zip(shift[key].split(':'), [60, 1])) for key in ['start', 'end']]
        hours = D(end - start) / 60
        if hours <= 0:
            raise ValueError('班次結束時間必須晚於開始時間')
        total += hours
        shift_date = day(shift['date'])
        shift_hours[shift_date] = shift_hours.get(shift_date, D(0)) + hours - (D(data['unpaid_break_hours']) if hours >= D(data['unpaid_break_threshold_hours']) else 0)
    scheduled[week['number']] = total
    paid[week['number']] = sum(shift_hours[d] for d in {day(s['date']) for s in week['shifts']})
for slip in data['payslips']:
    if D(slip['gross']) - D(slip['paye']) != D(slip['net']):
        raise ValueError('薪資單稅前、扣繳及稅後不一致')
    group = sum(scheduled[n] for n in slip['weeks'])
    for n in slip['weeks']:
        paid[n] = D(slip['hours']) * scheduled[n] / group
tax_summary = data.get('tax_summary')
if tax_summary:
    sunson_slips = [s for s in data['payslips'] if s.get('job', 'Sunson') == 'Sunson']
    checks = {
        'taxable_earnings': sum(D(s['gross']) for s in sunson_slips),
        'total_deductions': sum(D(s['paye']) for s in sunson_slips),
        'income_tax_deductions': D(tax_summary['total_deductions']) - D(tax_summary['acc_earners_levy']),
    }
    for key in ['taxable_earnings', 'total_deductions']:
        if checks[key] != D(tax_summary[key]):
            raise ValueError(f'稅務摘要與 Sunson 薪資單的 {key} 不一致')
    if checks['income_tax_deductions'] != D(tax_summary['income_tax_deductions']):
        raise ValueError('稅務摘要中的扣繳與 ACC 拆分不一致')
included = [w for w in weeks if w['number'] not in data['excluded_weeks']]
count = len(included)
scheduled_total = sum(scheduled[w['number']] for w in included)
paid_total = sum(paid[w['number']] for w in included)
average = paid_total / count
month = {k: v * frequency / 12 for k, v in payment(average * cycle / 7).items() if k != 'hours'}
month['saving'] = month['net'] - cost * 52 / 12
last_slip = max(data['payslips'], key=lambda s: s['pay_date'])
covered_by_job = {job['label']: max((day(s['period_end']) for s in data['payslips'] if s.get('job', 'Sunson') == job['label']), default=tax_start - timedelta(days=1)) for job in data['jobs']}
latest_week = max(weeks, key=lambda w: w['start'])
known_days = {day(w['start']) + timedelta(days=i) for w in weeks for i in range(7)}
sunson_end = day(data['jobs'][0]['end'])

# 各雇主分開估扣 PAYE；第二、第三份工作暫沿用同一發薪日與薪期。
future = []
payday = min(day(s['pay_date']) for s in data['payslips'])
while payday <= tax_end:
    period_end = payday - timedelta(days=data['pay_period_lag_days'])
    period_start = period_end - timedelta(days=cycle - 1)
    for job_index, job in enumerate(data['jobs']):
        start = max(period_start, day(job['start']), covered_by_job[job['label']] + timedelta(days=1))
        end = min(period_end, day(job['end']) if job['end'] else tax_end)
        if start > end:
            continue
        hours, estimated_days = D(0), 0
        for offset in range((end - start).days + 1):
            current = start + timedelta(days=offset)
            if job_index == 0 and current in known_days:
                hours += shift_hours.get(current, D(0))
            else:
                hours += average / 7
                estimated_days += 1
        future.append({'date': payday, 'job': job['label'], 'job_index': job_index, 'start': start, 'end': end,
                       'period_start': period_start, 'period_end': period_end, 'estimated_days': estimated_days, **payment(hours)})
    payday += timedelta(days=cycle)
actual = [s for s in data['payslips'] if tax_start <= day(s['pay_date']) <= tax_end]
annual_gross = sum(D(s['gross']) for s in actual) + sum(p['gross'] for p in future)
annual_paye = sum(D(s['paye']) for s in actual) + sum(p['paye'] for p in future)
annual_income_tax, annual_acc = income_tax(annual_gross), acc(annual_gross)
refund = annual_paye - annual_income_tax - annual_acc
scenarios = {n: {'net': month['net'] + refund / n, 'saving': month['saving'] + refund / n} for n in [6, 9]}
paydays = [p for p in future if p['job_index'] == 0]


def twd(amount):
    amount = D(amount).quantize(D('.01'), rounding=ROUND_HALF_UP)
    return f'<span data-nzd-to-twd="{fmt(amount, 2).replace(",", "")}">約 NT${fmt(amount * fx)}</span>'


blocks = {}
actual_gross = sum(D(s['gross']) for s in data['payslips'])
actual_paye = sum(D(s['paye']) for s in data['payslips'])
actual_net = sum(D(s['net']) for s in data['payslips'])
blocks['stats'] = f'''<section class="stats" aria-label="收支摘要">
  <article class="stat"><span class="stat-label">{number(len(data['payslips']))}期實領</span><strong class="stat-value">NZ${fmt(actual_net, 2)}</strong><span class="stat-twd">{twd(actual_net)}</span><span class="stat-note">稅前 NZ${fmt(actual_gross, 2)} · 扣繳 NZ${fmt(actual_paye, 2)}</span></article>
  <article class="stat"><span class="stat-label">一般月薪估值</span><strong class="stat-value">稅前 NZ${fmt(month['gross'])}<br>稅後 NZ${fmt(month['net'])}</strong><span class="stat-twd">稅後 {twd(month['net'])}</span><span class="stat-note">以最新{number(count)}週平均推估；未含退稅</span></article>
  <article class="stat"><span class="stat-label">月可存估值</span><strong class="stat-value">約 NZ${fmt(month['saving'])}</strong><span class="stat-twd">{twd(month['saving'])}</span><span class="stat-note">已扣固定生活費；未含退稅</span></article>
</section>'''
latest_start = day(latest_week['start'])
latest_end = latest_start + timedelta(days=6)
excluded = '、'.join(str(n) for n in data['excluded_weeks'])
blocks['quick-info'] = f'''<section class="quick-info" aria-label="工時與生活費">
  <article class="quick-card"><h2>{number(count)}週平均工時</h2><div class="quick-value"><strong>{fmt(scheduled_total / count, 2)} h</strong><span>排班／週</span><strong>{fmt(average, 2)} h</strong><span>計薪／週</span></div><p class="quick-note">已納入 {span(latest_start, latest_end)} 最新班表；第 {excluded} 週不列入。</p></article>
  <article class="quick-card"><h2>固定生活費</h2><div class="quick-value"><strong>NZ${fmt(cost)}／週</strong></div><p class="quick-note">房租、油錢與餐費。</p></article>
</section>'''
blocks['refund'] = f'''<section class="refund" aria-labelledby="refund-title">
  <div class="refund-head"><h2 id="refund-title">2027 退稅預估</h2><strong class="refund-total">約 NZ${fmt(refund)}</strong></div>
  <p>{twd(refund)} · 已依最新{number(count)}週平均重算</p>
  <div class="scenario-line"><span>攤 6 個月：月存約 <b>NZ${fmt(scenarios[6]['saving'])}</b></span><span>攤 9 個月：月存約 <b>NZ${fmt(scenarios[9]['saving'])}</b></span></div>
  <p>沿用後續工作日期與雙週給薪假設；實際薪期仍待確認。退稅是一次性收入。</p>
</section>'''
shift_cards = ''.join(f'<article class="shift"><strong>{md(day(s["date"]))}（{weekday(day(s["date"]))}）</strong><span>{escape(s["start"])}–{escape(s["end"])}{(" · " + escape(s["role"])) if s.get("role") else ""}</span></article>' for s in latest_week['shifts'])
blocks['latest'] = f'''<h2>已公布班表 · {span(latest_start, latest_end)}</h2><p class="intro">信箱通知於 {md(day(data['latest_roster_published']))} 公布；以下是預定班次，尚非出勤實績。</p>
<div class="shifts">{shift_cards}</div><p class="plan-note">排班 {fmt(scheduled[latest_week['number']], 2)} 小時，預估計薪 {fmt(paid[latest_week['number']], 2)} 小時。預設每班 {data['unpaid_break_threshold_hours']} 小時以上扣 {fmt(D(data['unpaid_break_hours']) * 60)} 分鐘無薪休息。</p>'''
publication = []
next_week = latest_start + timedelta(days=7)
while next_week <= sunson_end:
    saturday, sunday = next_week - timedelta(days=2), next_week - timedelta(days=1)
    cutoff = f'；上班至 {md(sunson_end)}' if next_week + timedelta(days=6) > sunson_end else ''
    publication.append(f'<div class="publish-item"><strong>{md(saturday)}（六）–{md(sunday)}（日）</strong><span>{span(next_week, next_week + timedelta(days=6))} 班表{cutoff}</span></div>')
    next_week += timedelta(days=7)
blocks['publish'] = '<h2>預計公布班表</h2><div class="publish-list">' + ''.join(publication) + '</div><p class="plan-note">依每週六、日公布的安排推估；實際公告日期可能變動。</p>'
latest_payment = next((p for p in paydays if p['period_start'] <= latest_start <= p['period_end']), None)
latest_pay_note = f'最新 {span(latest_start, latest_end)} 班表計入 {md(latest_payment["date"])} 預估。' if latest_payment else '最新班表已納入平均；已領薪部分依實績。'
cards = []
for p in paydays:
    period = span(p['period_start'], p['period_end']) if p['end'] == p['period_end'] else f'末期估至 {md(p["end"])}'
    basis = '已公布班表' if p['estimated_days'] == 0 else '最新平均推估；待班表更新'
    cards.append(f'<article class="planned-pay"><div class="planned-pay-head"><strong>{md(p["date"])}（{weekday(p["date"])}）</strong><span>{period}</span></div><p class="quick-note">計薪 {fmt(p["hours"], 2)} h · {basis}</p><div class="planned-values"><span>稅前<b>NZ${fmt(p["gross"])}</b></span><span>稅後<b>NZ${fmt(p["net"])}</b></span></div></article>')
blocks['paydays'] = '<div class="planned-payments">' + ''.join(cards) + f'</div><p class="plan-note">金額含 8% 假期加給；稅後依 M 稅碼及 ACC 推估，畫面取整至元。{latest_pay_note}未公布部分按最新平均及有效日數／7 暫估。後續班表、扣繳與末期調整可能改變金額。</p>'
actual_cards = []
for s in data['payslips']:
    cells = [('計薪', fmt(D(s['hours']), 2) + ' h'), ('稅前', 'NZ$' + fmt(D(s['gross']), 2)), ('實領', 'NZ$' + fmt(D(s['net']), 2))]
    body = ''.join(f'<div class="pay-cell"><small>{label}</small><b>{value}</b></div>' for label, value in cells)
    actual_cards.append(f'<article class="pay"><div class="pay-top"><strong>{md(day(s["pay_date"]))} 發薪</strong><span>{span(day(s["period_start"]), day(s["period_end"]))}</span></div><div class="pay-grid">{body}</div><div class="quick-note">PAYE NZ${fmt(D(s["paye"]), 2)}</div></article>')
blocks['actual'] = '<div class="pays">' + ''.join(actual_cards) + '</div>'
week_cards = []
covered_numbers = {n for s in data['payslips'] for n in s['weeks']}
for w in weeks:
    n, start = w['number'], day(w['start'])
    groups = OrderedDict()
    for s in w['shifts']:
        groups.setdefault((s['start'], s['end']), []).append(md(day(s['date'])))
    times = '；'.join('、'.join(dates) + '　' + begin + '–' + end for (begin, end), dates in groups.items()) + '。'
    status = '薪資單' if any(s['weeks'] == [n] for s in data['payslips']) else '拆分估算' if n in covered_numbers else '已公布班表' if start + timedelta(days=6) > asof else '待發薪核對'
    css = 'actual' if status == '薪資單' else 'pending' if status == '待發薪核對' else ''
    note = f'第 {n} 週不列入平均。' if n in data['excluded_weeks'] else ''
    if n not in covered_numbers:
        note += '按預設無薪休息估計，待薪資單核對。'
    week_cards.append(f'<details class="week"><summary><span class="week-name">第 {n} 週 · {span(start, start + timedelta(days=6))}</span><span class="week-hours">排班 {fmt(scheduled[n], 2)} h · 計薪約 {fmt(paid[n], 2)} h</span><span class="status {css}">{status}</span></summary><div class="week-detail"><p>{times}{note}</p></div></details>')
chosen = '、'.join(str(w['number']) for w in included)
blocks['weeks'] = '<div class="week-list">' + ''.join(week_cards) + f'</div><p class="intro" style="margin-top:12px">平均採第 {chosen} 週：排班共 {fmt(scheduled_total, 2)} h，計薪估值共 {fmt(paid_total, 2)} h。已領薪週按薪資單時數拆分；未領薪週依班表扣無薪休息估算。計算保留未四捨五入時數。</p>'
blocks['salary-method'] = f'''<h2>薪資與生活費</h2><ul class="assumptions">
<li>累計前 20 個計薪小時 NZ${data['first_20_hour_rate']}／小時，其後 NZ${data['hourly_rate']}／小時；另加 8% 假期加給。後續預估採正常時薪。</li>
<li>每班 {data['unpaid_break_threshold_hours']} 小時以上預設扣 {fmt(D(data['unpaid_break_hours']) * 60)} 分鐘無薪休息；已領薪時數與金額依薪資單，不額外扣除。</li>
<li>最新{number(count)}週平均計薪 {fmt(average, 2)} h。月化稅前 NZ${fmt(month['gross'], 2)}、PAYE（含 ACC）NZ${fmt(month['paye'], 2)}、稅後 NZ${fmt(month['net'], 2)}；按雙週給薪估扣稅後，乘 26 ÷ 12 月化。</li>
<li>生活費每週 NZ${fmt(cost)}：房租 ${data['weekly_costs']['rent']}、油錢 ${data['weekly_costs']['fuel']}、餐費 ${data['weekly_costs']['food']}。月化 × 52 ÷ 12，月可存 NZ${fmt(month['saving'], 2)}。</li></ul>'''
blocks['refund-method'] = f'''<h2>退稅模型</h2><ul class="assumptions">
<li>2026/27 年度以 {tax_start.year}/4/1–{tax_end.year}/3/31 發薪日為界。預估薪資 NZ${fmt(annual_gross, 2)}、預扣 PAYE NZ${fmt(annual_paye, 2)}；全年所得稅 NZ${fmt(annual_income_tax, 2)}＋ACC NZ${fmt(annual_acc, 2)}＝NZ${fmt(annual_income_tax + annual_acc, 2)}。差額預估退稅 NZ${fmt(refund, 2)}。</li>
<li>已領薪資及 PAYE 為起點；Sunson 未領薪部分依已公布班表或最新平均。第二份暫定 2026/11/20–2027/2/19；第三份 2027/3/15 起。兩份皆暫沿用相同薪資與雙週薪期，逐雇主估扣 PAYE。</li>
<li>同一雙週週期下，第三份在 3/26 暫估領 3/15–21 的薪資；3/22 起如於 4/9 發薪，歸下一年度。其他雇主實際發薪日及工作日期改變時，本模型須同步重算。<a href="https://www.taxpolicy.ird.govt.nz/-/media/project/ir/tp/publications/2015/2015-dd-mts-4-paye-gst/2015-dd-mts-4-paye-gst-pdf.pdf" target="_blank" rel="noopener noreferrer">IRD 薪資入帳年度說明</a>。</li>
<li>攤 6 個月：每月加 NZ${fmt(refund / 6, 2)}，含退稅月領 NZ${fmt(scenarios[6]['net'], 2)}、月存 NZ${fmt(scenarios[6]['saving'], 2)}。攤 9 個月：每月加 NZ${fmt(refund / 9, 2)}，月領 NZ${fmt(scenarios[9]['net'], 2)}、月存 NZ${fmt(scenarios[9]['saving'], 2)}。月領均為稅後加一次性退稅平攤，稅前月薪不變；9 個月情境未含 2028 年退稅，不代表各月實際入帳。</li>
<li>本模型僅包含已列的紐西蘭薪資，未納入利息、其他收入及抵免；不是 IRD 結算結果。<a href="https://www.ird.govt.nz/income-tax-assessments" target="_blank" rel="noopener noreferrer">IRD 年度結算</a>。</li></ul>'''

blocks['actual-source'] = f'''<li>截至 {escape(tax_summary['as_of'].replace('-', '/'))} 的稅務摘要與薪資單合計一致：應稅 NZ${fmt(D(tax_summary['taxable_earnings']), 2)}、PAYE／扣繳 NZ${fmt(D(tax_summary['total_deductions']), 2)}（含 ACC NZ${fmt(D(tax_summary['acc_earners_levy']), 2)}；扣除 ACC 後 NZ${fmt(D(tax_summary['income_tax_deductions']), 2)}）。歷史週拆分及未領薪週工時為估值。</li>''' if tax_summary else ''
blocks['fx-method'] = f'''<li>紐幣收入、存款與退稅換回台幣，依星展即期買入 {escape(data['fx']['buy_rate'])} 換算；同一牌告即期賣出 {escape(data['fx']['sell_rate'])}。牌告時間：{escape(data['fx']['quoted_at'])}。<a href="{escape(data['fx']['source_url'], quote=True)}" target="_blank" rel="noopener noreferrer">星展牌告來源</a>。此為查詢快照，非自動即時更新；實際交易以當時牌告為準。</li>'''

source = (ROOT / 'index.html').read_text()
for name, content in blocks.items():
    pattern = f'(<!-- model:{name}:start -->).*?(<!-- model:{name}:end -->)'
    source, matches = re.subn(pattern, lambda m: m[1] + '\n' + content + '\n' + m[2], source, flags=re.S)
    if matches != 1:
        raise ValueError(f'{name} 區塊標記數量不正確；不寫入網頁')
source = re.sub(r'(<span class="summary-hint plan-hint">).*?(</span>)', lambda m: m[1] + ((md(paydays[0]['date']) + ' · 稅後約 NZ$' + fmt(paydays[0]['net'])) if paydays else '待確認末期結算') + m[2], source)
source = re.sub(r'依 \d{1,2}/\d{1,2} 實際發薪日起每 14 天推算', f'依 {md(day(last_slip["pay_date"]))} 實際發薪日起每 14 天推算', source)
source = re.sub(r'[一二三四五六七八九十0-9]+期薪資 · [^<]*週排班', f'{number(len(data["payslips"]))}期薪資 · {number(len(weeks))}週排班', source)
source = re.sub(r'<div class="badges">.*?</div>', f'<div class="badges"><span class="badge">薪資實績至 {last_slip["pay_date"].replace("-", "/")}</span><span class="badge">班表更新至 {data["updated"].replace("-", "/")}</span></div>', source)
source = re.sub(r'<footer>.*?</footer>', f'<footer>薪資實績截至 {last_slip["pay_date"].replace("-", "/")} · 班表及全部估值更新至 {data["updated"].replace("-", "/")} · 台幣按標示牌告換算</footer>', source)
source = re.sub(r'(id="fxRate"[^>]*value=")[^"]+', lambda m: m[1] + data['fx']['buy_rate'], source)
source = re.sub(r'(<small id="fxHelp">).*?(</small>)', lambda m: m[1] + escape('星展即期買入（紐幣換回台幣） · ' + data['fx']['quoted_at'] + '；即期賣出 ' + data['fx']['sell_rate'] + '。牌告快照，非自動即時更新。') + m[2], source)
source = re.sub(r'(<meta name="description" content=")[^"]+', lambda m: m[1] + 'Sunson 紐西蘭工作排班與薪資收支摘要。薪資實績截至 ' + last_slip['pay_date'].replace('-', '/') + '，班表及全部估值更新至 ' + data['updated'].replace('-', '/') + '。', source)
source = source.replace('有效日数', '有效日數').replace('双週', '雙週')
(ROOT / 'index.html').write_text(source)
print(json.dumps({'included_weeks': [w['number'] for w in included], 'average_paid_hours': str(average),
                  'annual_gross': str(annual_gross), 'annual_paye': str(annual_paye), 'annual_tax_and_acc': str(annual_income_tax + annual_acc),
                  'refund': str(refund), 'six_month_net': str(scenarios[6]['net']), 'six_month_saving': str(scenarios[6]['saving']),
                  'nine_month_net': str(scenarios[9]['net']), 'nine_month_saving': str(scenarios[9]['saving'])}, ensure_ascii=False, indent=2))
