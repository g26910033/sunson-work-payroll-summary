"""由去識別化資料產生靜態網站；僅使用 Python 標準函式庫。"""
import json
import re
from collections import OrderedDict
from datetime import date, timedelta
from decimal import Decimal as D, ROUND_HALF_UP, ROUND_DOWN
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent
data = json.loads((ROOT / 'forecast-data.json').read_text())
day = date.fromisoformat
asof = day(data['updated'])
cent = lambda n: D(n).quantize(D('.01'), rounding=ROUND_HALF_UP)
fmt = lambda n, places=0: f'{D(n).quantize(D(1).scaleb(-places), rounding=ROUND_HALF_UP):,.{places}f}'
md = lambda d: f'{d.month}/{d.day}'
span = lambda a,b: md(a) + ('–' + md(b) if a != b else '')
weekday = lambda d: '一二三四五六日'[d.weekday()]
fx = D(data['fx']['buy_rate'])
cycle = data['pay_cycle_days']
tax_start, tax_end = day(data['tax_year_start']), day(data['tax_year_end'])
data['bank_interest']['forecast_days'] = max(0, (tax_end-asof).days+1)


def income_tax(amount):
    total, lower = D(0), D(0)
    for upper, rate in [(15600,'.105'),(53500,'.175'),(78100,'.30'),(180000,'.33')]:
        total += max(D(0), min(D(amount), D(upper)) - lower) * D(rate)
        lower = D(upper)
    return total + max(D(0), D(amount) - lower) * D('.39')


def acc(amount):
    return min(D(amount), D(data['acc_cap'])) * D(data['acc_rate'])


def withholding(gross, days=14):
    """IRD 2026/27 M：年化截元；每週扣繳截至分，再按薪期換算。"""
    frequency = D(364) / days
    annual = (D(gross) * frequency).to_integral_value(rounding=ROUND_DOWN)
    weekly = ((income_tax(annual) + acc(annual)) / 52).quantize(D('.01'), rounding=ROUND_DOWN)
    return (weekly * 52 / frequency).quantize(D('.01'), rounding=ROUND_DOWN)


def payment(hours, days=14):
    ordinary = cent(D(hours) * D(data['hourly_rate']))
    holiday = cent(ordinary * D(data['holiday_rate']))
    gross = ordinary + holiday
    paye = withholding(gross, days)
    return {'hours':D(hours), 'ordinary':ordinary, 'holiday':holiday, 'gross':gross, 'paye':paye, 'net':gross-paye}


weeks = data['weeks']
scheduled, paid, shift_hours = {}, {}, {}
for w in weeks:
    total = D(0)
    for s in w['shifts']:
        d = day(s['date'])
        if not day(w['start']) <= d <= day(w['start']) + timedelta(days=6):
            raise ValueError('班次日期不在週內')
        start,end = [sum(int(n)*unit for n,unit in zip(s[k].split(':'), [60,1])) for k in ['start','end']]
        hours = D(end-start)/60
        if hours <= 0:
            raise ValueError('班次時間不正確')
        total += hours
        shift_hours[d] = shift_hours.get(d,D(0)) + hours - (D(data['unpaid_break_hours']) if hours >= D(data['unpaid_break_threshold_hours']) else 0)
    scheduled[w['number']] = total
    paid[w['number']] = sum(shift_hours[day(s['date'])] for s in w['shifts'])
for s in data['payslips']:
    if D(s['gross'])-D(s['paye']) != D(s['net']):
        raise ValueError('薪資單金額不一致')
    if withholding(D(s['gross']),cycle) != D(s['paye']):
        raise ValueError('M 扣繳公式未重現薪資單；請查核稅碼與薪期')
    group = sum(scheduled[n] for n in s['weeks'])
    for n in s['weeks']:
        paid[n] = D(s['hours']) * scheduled[n] / group
summary = data['tax_summary']
summary_slips = [s for s in data['payslips'] if s.get('job','Sunson') == 'Sunson' and tax_start <= day(s['pay_date']) <= day(summary['as_of'])]
if sum(D(s['gross']) for s in summary_slips) != D(summary['taxable_earnings']) or sum(D(s['paye']) for s in summary_slips) != D(summary['total_deductions']):
    raise ValueError('薪資單與稅務摘要不一致')
if D(summary['total_deductions'])-D(summary['acc_earners_levy']) != D(summary['income_tax_deductions']):
    raise ValueError('稅務摘要 ACC 拆分不一致')
included = [w for w in weeks if w['number'] not in data['excluded_weeks']]
count = len(included)
scheduled_total = sum(scheduled[w['number']] for w in included)
paid_total = sum(paid[w['number']] for w in included)
average = paid_total / count
weekly_cost = sum(D(v) for v in data['weekly_costs'].values())
month = {k:v*D(26)/12 for k,v in payment(average*2).items() if k in ['gross','paye','net']}
month['cost'] = weekly_cost*52/12
month['saving'] = month['net']-month['cost']
actual = [s for s in data['payslips'] if tax_start <= day(s['pay_date']) <= tax_end]
actual_totals = {k:sum(D(s[k]) for s in actual) for k in ['hours','gross','paye','net']}
last_slip = max(actual,key=lambda s:s['pay_date'])
latest_week = max(weeks,key=lambda w:w['start'])
latest_start = day(latest_week['start'])
latest_end = latest_start+timedelta(days=6)
sunson_end = day(data['jobs'][0]['end'])
known_days = {day(w['start'])+timedelta(days=i) for w in weeks for i in range(7)}
covered = {j['label']:max((day(s['period_end']) for s in actual if s.get('job','Sunson')==j['label']),default=tax_start-timedelta(days=1)) for j in data['jobs']}
anchor = min(day(s['pay_date']) for s in actual)


def forecast(indices, hours=average, later_cycle=14):
    future=[]
    for ji in indices:
        job=data['jobs'][ji]
        days = cycle if ji==0 else later_cycle
        payday=anchor
        while payday <= tax_end:
            period_end=payday-timedelta(days=data['pay_period_lag_days'])
            period_start=period_end-timedelta(days=days-1)
            start=max(period_start,day(job['start']),covered[job['label']]+timedelta(days=1))
            end=min(period_end,day(job['end']) if job['end'] else tax_end)
            if start<=end:
                known_hours,unknown_days=D(0),0
                for offset in range((end-start).days+1):
                    d=start+timedelta(days=offset)
                    if ji==0 and d in known_days:
                        known_hours += shift_hours.get(d,D(0))
                    else:
                        unknown_days += 1
                # 一次計算整期未知日數，避免逐日循環小數破壞半分取位。
                total_hours=known_hours+D(hours)*unknown_days/7
                future.append({'pay_date':payday,'job_index':ji,'start':start,'end':end,'period_start':period_start,'period_end':period_end,'unknown_days':unknown_days,**payment(total_hours,days)})
            payday+=timedelta(days=days)
    gross=actual_totals['gross']+sum(p['gross'] for p in future)
    paye=actual_totals['paye']+sum(p['paye'] for p in future)
    interest=D(data['bank_interest']['annual_gross'] or '0')
    rwt=D(data['bank_interest']['annual_rwt'] or '0')
    annual_tax=cent(income_tax(gross+interest))
    levy=cent(acc(gross))
    return {'gross':gross,'paye':paye,'interest':interest,'rwt':rwt,'income_tax':annual_tax,'acc':levy,'refund':paye+rwt-annual_tax-levy,'future':sorted(future,key=lambda p:(p['pay_date'],p['job_index']))}


plans=[('sunson','只做 Sunson','11/19 後沒有其他薪資',[0]),('two_jobs','接第二份','續做至 2/19，3 月不工作',[0,1]),('planned','原規劃三份','再從 3/15 開始第三份',[0,1,2])]
models={}
for key,label,description,indices in plans:
    models[key]={'id':key,'label':label,'description':description,**forecast(indices)}
base=models['planned']
future_paydays=[p for p in base['future'] if p['job_index']==0]
sensitivity=[('原規劃，未公布班次 20 h／週',forecast([0,1,2],D(20))),(f'原規劃，平均 {fmt(average,2)} h／週',base),('原規劃，未公布班次 32 h／週',forecast([0,1,2],D(32))),('後兩份改為週薪（日期相同）',forecast([0,1,2],average,7))]


def twd(amount):
    amount=cent(amount)
    return f'<span data-nzd-to-twd="{amount}">約 NT${fmt(amount*fx)}</span>'


def table_row(label,value,field=''):
    attr=f' data-tax-field="{field}"' if field else ''
    text='金額未提供；基準未計' if field=='interest' else 'NZ$'+fmt(value,2)
    return f'<tr><th scope="row">{label}</th><td{attr}>{text}</td></tr>'


blocks={}
blocks['overview']=f'''<div class="overview-grid">
<article class="card actual-card"><div class="card-top"><h3>累計實領</h3><span class="tag actual">已核對 {len(actual)} 期</span></div><p class="big-number">NZ${fmt(actual_totals['net'],2)}</p><p class="twd">{twd(actual_totals['net'])}</p><p class="muted">稅前 {fmt(actual_totals['gross'],2)} · PAYE {fmt(actual_totals['paye'],2)}</p><a class="card-link" href="#payroll"><span>{md(day(last_slip['pay_date']))} 本期實領</span><b>NZ${fmt(D(last_slip['net']),2)}</b></a></article>
<article class="card budget-card"><div class="card-top"><h3>一般月份</h3><span class="tag estimate">未含退稅</span></div><div class="salary-pair"><div><span>稅前</span><strong>NZ${fmt(month['gross'])}</strong></div><div><span>稅後</span><strong>NZ${fmt(month['net'])}</strong></div></div><div class="budget-bar" role="img" aria-label="稅後月收入約 {fmt(month['net'])} 元，其中生活費 {fmt(month['cost'])} 元，可存 {fmt(month['saving'])} 元"><span style="width:{month['cost']/month['net']*100:.2f}%"></span></div><div class="budget-line"><span>生活費 <b>NZ${fmt(month['cost'])}</b></span><span>月可存 <b>NZ${fmt(month['saving'])}</b></span></div><p class="twd">可存 {twd(month['saving'])}</p></article></div>
<p class="basis-line">最新 {count} 週平均（第 {min(w["number"] for w in included)}–{max(w["number"] for w in included)} 週）：排班 <b>{fmt(scheduled_total/count,2)} h</b>／計薪 <b>{fmt(average,2)} h</b>。含第 5 週，只排除到職的第 1 週。</p>'''
radios=''.join(f'<label class="plan-option"><input type="radio" name="workPlan" value="{key}" {"checked" if key=="planned" else ""}><span><b>{label}</b><small>{description}</small></span></label>' for key,label,description,_ in plans)
blocks['refund']=f'''<fieldset class="plan-options"><legend>後續工作怎麼接？</legend>{radios}</fieldset>
<div class="refund-hero"><div><span class="overline">2026/27 年度 · 年度結算估值</span><p id="refundValue" class="refund-number">NZ${fmt(base['refund'])}</p><p id="refundTwd" class="twd" data-nzd-to-twd="{cent(base['refund'])}">約 NT${fmt(cent(base['refund'])*fx)}</p></div><p id="refundBasis" class="refund-context">原規劃三份工作；後兩份尚未確定。<br>尚未含 ASB 實際利息與 RWT。</p></div>
<p class="notice">不同續工情境約 <b>NZ${fmt(min(m['refund'] for m in models.values()))}–{fmt(max(m['refund'] for m in models.values()))}</b>。這是工作安排的比較，實際退稅仍隨薪資、扣繳與其他收入變動。</p>'''
scenario_rows=''.join(f'<tr><th scope="row">{label}</th><td>NZ${fmt(models[key]["gross"])}</td><td><b>NZ${fmt(models[key]["refund"])}</b></td></tr>' for key,label,_,_ in plans)
sensitivity_rows=''.join(f'<tr><th scope="row">{label}</th><td>NZ${fmt(m["refund"],2)}</td></tr>' for label,m in sensitivity)
blocks['refund-detail']=f'''<div class="table-wrap"><table><caption>工作安排情境（均含已領薪資）</caption><thead><tr><th>安排</th><th>年度稅前</th><th>退稅估值</th></tr></thead><tbody>{scenario_rows}</tbody></table></div>
<p class="muted">第二份暫定 2026/11/20–2027/2/19；第三份暫定 2027/3/15 起。條件沿用相同時薪、平均工時、M 稅碼及雙週發薪，均屬假設。</p>
<h3>所選情境的計算</h3><table class="math-table"><tbody>{table_row('年度應稅薪資',base['gross'],'gross')}{table_row('薪資已扣及預估 PAYE（含 ACC）',base['paye'],'paye')}{table_row('利息／已扣 RWT',D(0),'interest')}{table_row('全年應繳所得稅',base['income_tax'],'income_tax')}{table_row('全年薪資 ACC',base['acc'],'acc')}{table_row('PAYE＋RWT－所得稅－ACC',base['refund'],'refund')}</tbody></table>
<p class="muted">PAYE 包含 ACC，不能把扣繳全數視為可退所得稅。首末不足完整薪期仍依正常雙週算扣繳；各雇主分開計算。</p>
<h3>工時與薪期改變時</h3><table><tbody>{sensitivity_rows}</tbody></table><p class="muted">20／32 小時只替換未公布部分，保留已公布班表及實績。薪期比較保留 Sunson 雙週、僅改後兩份；不是保證範圍。</p>
<h3>把退稅攤回每月</h3><table><thead><tr><th>攤回方式</th><th>含退稅月領</th><th>月可存</th></tr></thead><tbody>{''.join(f'<tr><th scope="row">{n} 個月</th><td data-spread-net="{n}">NZ${fmt(month["net"]+base["refund"]/n)}</td><td data-spread-saving="{n}">NZ${fmt(month["saving"]+base["refund"]/n)}</td></tr>' for n in [6,9])}</tbody></table><p class="muted">僅供月平均比較；稅前月薪不變，退稅會在結算後入帳。9 個月情境未計下一稅年度可能退稅。</p>'''
blocks['bank']=f'''<div class="bank-note"><div><span class="overline">已確認帳戶</span><h3>ASB Savings On Call <strong>{fmt(D(data["bank_interest"]["annual_rate"])*100,2)}% p.a.</strong></h3></div><span class="tag estimate">利息金額待確認</span></div><p>可隨時動用，所有餘額同率；按日計息、每月入帳。{fmt(D(data["bank_interest"]["annual_rate"])*100,2)}% 自 {data["bank_interest"]["effective_at"]} 生效，於 {escape(data['bank_interest']['checked_on'])} 查核。<a href="{escape(data['bank_interest']['source_url'],quote=True)}" target="_blank" rel="noopener noreferrer">ASB 官方利率</a>。</p><p class="muted">每 NZ$1,000 平均餘額，一年稅前利息約 NZ${fmt(D(data["bank_interest"]["annual_rate"])*1000,2)}。下方只估 {md(asof)}–{md(tax_end)} 的 {data['bank_interest']['forecast_days']} 天；以現行利率維持不變為假設。過去已累積但未入帳利息仍需補入；年度金額以 ASB／myIR 為準。輸入留在此裝置，不會上傳。</p>
<div class="input-grid"><label>未來期間平均餘額（NZ$）<input id="bankBalance" type="number" min="0" step="100" placeholder="例如 5,000" inputmode="decimal"></label><label>RWT 稅率（請核對實際設定）<select id="bankRwt"><option value="0.105">10.5%</option><option value="0.175" selected>17.5%（暫用假設）</option><option value="0.30">30%</option><option value="0.33">33%</option><option value="0.39">39%</option><option value="0.45">45%</option></select></label></div><p id="interestImpact" class="input-feedback" aria-live="polite">利息與 RWT 金額未提供，目前退稅先依薪資估算。</p>
<details class="disclosure"><summary>補入截至目前已收利息與 RWT</summary><div class="disclosure-body"><div class="input-grid"><label>已收稅前利息（NZ$）<input id="interestIncome" type="number" min="0" step="0.01" placeholder="依 ASB 或 myIR 金額" inputmode="decimal"></label><label>已扣 RWT（NZ$）<input id="interestRwt" type="number" min="0" step="0.01" placeholder="依 ASB 或 myIR 金額" inputmode="decimal"></label></div></div></details><p class="muted">居民利息加進所得稅，不計 ACC；RWT 再抵扣。若邊際稅率與 RWT 同為 17.5%，小額利息通常不改變退稅。若為非稅務居民，需改查 NRWT。<a href="https://www.ird.govt.nz/rwt-rate" target="_blank" rel="noopener noreferrer">IRD 利息扣繳規則</a></p>'''
shifts=[]
for s in latest_week['shifts']:
    d=day(s['date'])
    state='預定班次' if d>=asof else '班表日期已過 · 待薪資單'
    shifts.append(f'<li><div class="shift-date"><b>{md(d)}</b><span>週{weekday(d)}</span></div><div><strong>{s["start"]}–{s["end"]}</strong><small>{escape(s.get("role",""))}</small></div><span class="tag estimate">{state}</span></li>')
blocks['roster']=f'''<div class="subhead"><h3>最新班表 {span(latest_start,latest_end)}</h3><span class="muted">{md(day(data['latest_roster_published']))} 公布</span></div><ul class="shift-list">{''.join(shifts)}</ul><p class="muted">排班 {fmt(scheduled[latest_week['number']],2)} h，預估計薪 {fmt(paid[latest_week['number']],2)} h。每班 4 小時以上預設扣 30 分鐘無薪休息；薪資尚待核對。</p>'''
payrows=[]
for p in future_paydays:
    period=span(p['period_start'],p['period_end']) if p['end']==p['period_end'] else f'末期至 {md(p["end"])}'
    payrows.append(f'<tr><th scope="row"><b>{md(p["pay_date"])}</b><span class="pay-weekday">（{weekday(p["pay_date"])}）</span><small>{period}</small></th><td>NZ${fmt(p["gross"])}</td><td><b>NZ${fmt(p["net"])}</b></td></tr>')
blocks['paydays']=f'''<table class="payday-table"><thead><tr><th>發薪日</th><th>稅前</th><th>稅後</th></tr></thead><tbody>{''.join(payrows)}</tbody></table><p class="muted">已公布至 {span(latest_start,latest_end)} 的班次已計入相應薪期；未公布部分按平均 {fmt(average,2)} h／週推估。11/19 為最後上班日；尾薪暫列原薪期 12/4，若雇主提前結清會再更新。</p>'''
publications=[]
start=latest_start+timedelta(days=7)
while start<=sunson_end:
    publications.append(f'<li><b>{span(start-timedelta(days=2),start-timedelta(days=1))}</b><span>公布 {span(start,start+timedelta(days=6))} 班表</span></li>')
    start+=timedelta(days=7)
blocks['publish']='<ul class="publication-list">'+''.join(publications)+'</ul><p class="muted">依每週六、日公布的安排推估；最後一週只工作至 11/19。</p>'
actual_cards=[]
for i,s in enumerate(sorted(actual,key=lambda s:s['pay_date'],reverse=True)):
    actual_cards.append(f'''<details class="pay-record" {"open" if i==0 else ""}><summary><span><b>{md(day(s['pay_date']))} 發薪</b><small>{span(day(s['period_start']),day(s['period_end']))}</small></span><span class="record-net">實領 NZ${fmt(D(s['net']),2)}</span></summary><div class="record-body"><dl class="record-grid"><div><dt>計薪</dt><dd>{fmt(D(s['hours']),2)} h</dd></div><div><dt>稅前</dt><dd>NZ${fmt(D(s['gross']),2)}</dd></div><div><dt>PAYE（含 ACC）</dt><dd>NZ${fmt(D(s['paye']),2)}</dd></div></dl></div></details>''')
blocks['actual']='<div class="pay-records">'+''.join(actual_cards)+'</div>'
blocks['tax-proof']=f'<p>{len(summary_slips)} 期薪資與 {md(day(summary["as_of"]))} 稅務摘要相符：應稅 <b>NZ${fmt(D(summary["taxable_earnings"]),2)}</b>，PAYE <b>NZ${fmt(D(summary["total_deductions"]),2)}</b>；其中 ACC NZ${fmt(D(summary["acc_earners_levy"]),2)}、所得稅 NZ${fmt(D(summary["income_tax_deductions"]),2)}。</p>'
covered_numbers={n for s in actual for n in s['weeks']}
week_cards=[]
for w in reversed(weeks):
    n=w['number']; start=day(w['start']); groups=OrderedDict()
    for s in w['shifts']:
        groups.setdefault((s['start'],s['end']),[]).append(md(day(s['date'])))
    rows=''.join(f'<li><span>{"、".join(ds)}</span><b>{a}–{b}</b></li>' for (a,b),ds in groups.items())
    state='薪期拆估' if n in covered_numbers and n!=1 else '薪資單' if n==1 else '班表估計'
    note='本週不列入平均。' if n in data['excluded_weeks'] else ''
    week_cards.append(f'<details class="week-record"><summary><span><b>第 {n} 週 · {span(start,start+timedelta(days=6))}</b><small>排班 {fmt(scheduled[n],2)} h · 計薪約 {fmt(paid[n],2)} h</small></span><span class="tag {"actual" if state=="薪資單" else "estimate"}">{state}</span></summary><ul class="week-shifts">{rows}</ul>{f"<p class=muted>{note}</p>" if note else ""}</details>')
blocks['weeks']='<div class="week-records">'+''.join(week_cards)+f'</div><p class="muted">平均採第 {min(w["number"] for w in included)}–{max(w["number"] for w in included)} 週，排班共 {fmt(scheduled_total,2)} h、計薪共 {fmt(paid_total,2)} h。已領薪週按薪期實績比例拆分，單週仍屬估值；未領薪週才扣預設休息。</p>'
blocks['method']=f'''<div class="method-grid"><div><h3>薪資與預算</h3><ul><li>累計前 20 小時按 NZ${data['first_20_hour_rate']}，其後 NZ${data['hourly_rate']}；另加 8% 假期加給，已計入各期稅前。</li><li>未領薪每班滿 4 小時扣 30 分鐘；薪資單時數保留實績，不再扣一次。</li><li>一般月薪按雙週 ×26÷12；固定生活費每週 NZ${fmt(weekly_cost)}，月化 ×52÷12。房租 120、油錢 40、餐費 150／週。</li></ul></div><div><h3>退稅的年度與限制</h3><ul><li>只計 2026/4/1–2027/3/31 入帳薪資。依假設薪期，3/26 領 3/15–21；3/22 後若 4/9 領，歸下一稅年。</li><li>M 稅碼依 IRD 官方截尾規則；普通工資與 8% 加給分別取分。公式已重現五期實際 PAYE。</li><li>ACC 1.75%，上限 NZ$156,641；利息不課 ACC。現有薪資情境皆未達 IETC 最低 NZ$24,000，未加所得稅抵免。</li><li>未提供的其他收入、境外收入、居住身分與抵免可能影響結算；後續雇主、薪期、時薪與工時確定後須重算。</li></ul></div></div>'''
blocks['sources']='''<ul class="source-list"><li><a href="https://www.ird.govt.nz/-/media/project/ir/home/documents/digital-service-providers/software-providers/payroll-calculations-business-rules-specifications/payroll-calculations-and-business-rules-specification.pdf" target="_blank" rel="noopener noreferrer">IRD 2026/27 PAYE 公式 §5.2</a></li><li><a href="https://www.ird.govt.nz/income-tax/income-tax-for-individuals/tax-codes-and-tax-rates-for-individuals/tax-rates-for-individuals" target="_blank" rel="noopener noreferrer">所得稅級距</a> · <a href="https://www.ird.govt.nz/income-tax/income-tax-for-individuals/acc-clients-and-carers/acc-earners-levy-rates" target="_blank" rel="noopener noreferrer">ACC 費率</a></li><li><a href="https://www.ird.govt.nz/income-tax/income-tax-for-individuals/individual-tax-credits/independent-earner-tax-credit-ietc" target="_blank" rel="noopener noreferrer">IETC 資格</a> · <a href="https://www.ird.govt.nz/international-tax/individuals/tax-residency-status-for-individuals" target="_blank" rel="noopener noreferrer">稅務居民</a></li><li><a href="https://www.employment.govt.nz/pay-and-hours/pay-and-wages/final-pay" target="_blank" rel="noopener noreferrer">尾薪時程</a> · <a href="https://www.taxtechnical.ird.govt.nz/operational-positions/commissioners-operational-position-on-calculating-paye-on-holiday-pay" target="_blank" rel="noopener noreferrer">假期薪資扣繳</a></li><li><a href="https://www.ird.govt.nz/-/media/project/ir/home/documents/forms-and-guides/ir300---ir399/ir335/ir335.pdf" target="_blank" rel="noopener noreferrer">IRD 雇主指南（部分薪期）</a> · <a href="https://www.taxpolicy.ird.govt.nz/-/media/project/ir/tp/publications/2018/2018-other-mts-cabinet-papers/2018-other-mts-cabinet-papers-3-1-paye-pdf.pdf" target="_blank" rel="noopener noreferrer">薪資收現年度</a></li></ul>'''
blocks['freshness']=f'<span>薪資實績至 <b>{md(day(last_slip["pay_date"]))}</b></span><span>班表公布 <b>{md(day(data["latest_roster_published"]))}</b></span>'
blocks['fx']=f'''<div class="fx-heading"><label for="fxRate">紐幣換回台幣</label><span class="rate-input">NZ$1 = NT$ <input id="fxRate" type="number" min="0.01" max="1000" step="0.01" value="{data['fx']['buy_rate']}" inputmode="decimal" aria-describedby="fxHelp"></span></div><p id="fxHelp"><a href="{escape(data['fx']['source_url'],quote=True)}" target="_blank" rel="noopener noreferrer">星展即期買入 {data['fx']['buy_rate']}</a> · 同時賣出 {data['fx']['sell_rate']}<br>{escape(data['fx']['quoted_at'])} 牌告快照；可修改匯率試算。</p>'''
currency_month=''.join(f'<tr><th scope="row">{label}</th><td>{twd(month[key])}</td></tr>' for label,key in [('月薪稅前','gross'),('月薪稅後','net'),('每月生活費','cost'),('每月可存','saving')])
currency_actual=''.join(f'<li><b>{md(day(s["pay_date"]))} 已領</b><div><span><small>稅前</small>{twd(D(s["gross"]))}</span><span><small>稅後</small>{twd(D(s["net"]))}</span></div></li>' for s in sorted(actual,key=lambda s:s['pay_date'],reverse=True))
currency_future=''.join(f'<li><b>{md(p["pay_date"])} 預估</b><div><span><small>稅前</small>{twd(p["gross"])}</span><span><small>稅後</small>{twd(p["net"])}</span></div></li>' for p in future_paydays)
currency_spread=''.join(f'<li><b>攤回 {n} 個月</b><div><span><small>含退稅月領</small><span data-spread-net-twd="{n}" data-nzd-to-twd="{cent(month["net"]+base["refund"]/n)}">約 NT${fmt((month["net"]+base["refund"]/n)*fx)}</span></span><span><small>月可存</small><span data-spread-saving-twd="{n}" data-nzd-to-twd="{cent(month["saving"]+base["refund"]/n)}">約 NT${fmt((month["saving"]+base["refund"]/n)*fx)}</span></span></div></li>' for n in [6,9])
blocks['currency-details']=f'<p class="muted">依上方星展買入牌告或自行輸入的匯率換算，僅供參考。</p><h3>一般月份</h3><table><tbody>{currency_month}</tbody></table><h3>薪資單與後續發薪</h3><ul class="currency-comparison">{currency_future}{currency_actual}</ul><h3>所選退稅情境攤回每月</h3><ul class="currency-comparison">{currency_spread}</ul>'
client_models={k:{field:value for field,value in m.items() if field!='future'} for k,m in models.items()}
client={'scenarios':client_models,'monthly':month,'defaultPlan':'planned','interest':data['bank_interest'],'updated':data['updated']}
blocks['client-data']='<script id="forecastModel" type="application/json">'+json.dumps(client,ensure_ascii=False,default=str).replace('<','\\u003c')+'</script>'
source=(ROOT/'page-template.html').read_text()
for name,content in blocks.items():
    pattern=f'(<!-- model:{name}:start -->).*?(<!-- model:{name}:end -->)'
    source,matches=re.subn(pattern,lambda m:m[1]+'\n'+content+'\n'+m[2],source,flags=re.S)
    if matches!=1:
        raise ValueError(f'模板區塊 {name} 數量不是 1')
source=source.replace('{{UPDATED}}',data['updated'].replace('-','/')).replace('{{REVISION}}',data['model_revision']).replace('{{LAST_DAY}}',md(sunson_end))
(ROOT/'index.html').write_text(source)
print(json.dumps({'average':str(average),'monthly':month,'scenarios':client_models,'paydays':future_paydays},ensure_ascii=False,default=str,indent=2))
