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
sunson_job = data['jobs'][0]
sunson_end = day(sunson_job['end'])
sunson_roster_until = day(sunson_job.get('forecast_until', sunson_job['end']))
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
            end_limit=day(job['end']) if job['end'] else tax_end
            if job.get('forecast_until'):
                end_limit=min(end_limit,day(job['forecast_until']))
            end=min(period_end,end_limit)
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


plans=[('sunson','只做 Sunson','只計至 11/15 已公布班表，之後無其他薪資',[0]),('two_jobs','接第二份','續做至 2/19，3 月不工作',[0,1]),('planned','原規劃三份','再從 3/15 開始第三份',[0,1,2])]
models={}
for key,label,description,indices in plans:
    models[key]={'id':key,'label':label,'description':description,**forecast(indices)}
base=models['planned']
future_paydays=[p for p in base['future'] if p['job_index']==0]
pending_paydays=[]
if sunson_roster_until < sunson_end:
    payday=anchor
    while payday <= tax_end:
        period_end=payday-timedelta(days=data['pay_period_lag_days'])
        period_start=period_end-timedelta(days=cycle-1)
        start=max(period_start,sunson_roster_until+timedelta(days=1),day(sunson_job['start']),covered[sunson_job['label']]+timedelta(days=1))
        end=min(period_end,sunson_end)
        if start<=end:
            pending_paydays.append({'pay_date':payday,'start':start,'end':end})
        payday+=timedelta(days=cycle)
sensitivity=[('原規劃，未公布班次 20 h／週',forecast([0,1,2],D(20))),(f'原規劃，平均 {fmt(average,2)} h／週',base),('原規劃，未公布班次 32 h／週',forecast([0,1,2],D(32))),('後兩份改為週薪（日期相同）',forecast([0,1,2],average,7))]


def twd(amount):
    amount=cent(amount)
    return f'<small class="money-twd" data-nzd-to-twd="{amount}">約 NT${fmt(amount*fx)}</small>'


def money(amount, inline=False, nzd_id='', twd_id=''):
    amount=cent(amount)
    ids=f' id="{nzd_id}"' if nzd_id else ''
    converted=twd(amount).replace('class="money-twd"', f'id="{twd_id}" class="money-twd"') if twd_id else twd(amount)
    return f'<span class="money{" money-inline" if inline else ""}" data-money-value="{amount}"><span class="money-nzd"{ids}>NZ${fmt(amount,2)}</span>{converted}</span>'


def deduction_split(s):
    supplied=s.get('deduction_split')
    levy=D(supplied['acc']) if supplied else cent(D(s['gross'])*D(data['acc_rate']))
    tax=D(supplied['income_tax']) if supplied else D(s['paye'])-levy
    if tax+levy != D(s['paye']):
        raise ValueError('所得稅與 ACC 拆分未等於 PAYE')
    return tax,levy


def deduction_grid(paye, levy, label='拆分估算'):
    return '<dl class="deduction-grid">'+''.join(f'<div><dt>{name}</dt><dd>{money(value)}</dd></div>' for name,value in [('所得稅',D(paye)-D(levy)),('ACC 意外傷害保險費',levy),('合計扣繳 PAYE',paye)])+f'</dl><p class="split-note">{escape(label)}</p>'


def table_row(label,value,field=''):
    attr=f' data-tax-field="{field}"' if field else ''
    text='金額未提供；基準未計' if value is None else money(value)
    return f'<tr><th scope="row">{label}</th><td{attr}>{text}</td></tr>'


# 金額及扣繳拆分只影響呈現；既有 PAYE 與年度結算模型保持原值。
actual_levy=D(summary['acc_earners_levy'])+sum(deduction_split(s)[1] for s in actual if day(s['pay_date'])>day(summary['as_of']))
for model in models.values():
    model['prepaid_acc']=actual_levy+sum(cent(p['gross']*D(data['acc_rate'])) for p in model['future'])
    model['prepaid_income_tax']=model['paye']-model['prepaid_acc']
month_paye=cent(month['paye'])
month_levy=cent(month['gross']*D(data['acc_rate']))
blocks={}
next_pay=next((p for p in future_paydays if p['pay_date']>=asof),None)
if next_pay:
    blocks['overview']=f'''<div class="focus-grid"><article class="next-pay"><div class="focus-top"><h3>{md(next_pay['pay_date'])}（{weekday(next_pay['pay_date'])}）發薪</h3><span class="tag">預估</span></div><span class="overline">下次稅後實領</span><div class="big-number">{money(next_pay['net'])}</div><div class="focus-meta"><div><span>稅前</span>{money(next_pay['gross'])}</div><div><span>計薪工時</span><b>{fmt(next_pay['hours'],2)} h</b></div></div><a class="card-link" href="#paydays"><span>查看所得稅、ACC 與薪期</span><span aria-hidden="true">→</span></a></article><a class="mini-card" href="#budget"><h3>一般月可存</h3><div class="mini-amount">{money(month['saving'])}</div><small>未含退稅</small></a><a class="mini-card" href="#payroll"><h3>累計實領</h3><div class="mini-amount">{money(actual_totals['net'])}</div><small>已核對 {len(actual)} 期薪資單</small></a></div>'''
else:
    blocks['overview']='<div class="card"><h3>發薪資料待更新</h3><p>請查看已領薪資與歷史班表。</p></div>'
blocks['budget']=f'''<div class="budget-layout"><div><div class="salary-pair"><div><label>月薪稅前</label>{money(month['gross'])}</div><div><label>月薪稅後</label>{money(month['net'])}</div></div><p class="muted">一般工作月份的平均估算，未含退稅。</p></div><div><div class="budget-bar" role="img" aria-label="生活費與可存金額占稅後月收入的比例"><span style="width:{month['cost']/month['net']*100:.2f}%"></span></div><div class="budget-line"><div><label>生活費</label>{money(month['cost'])}</div><div><label>月可存</label>{money(month['saving'])}</div></div></div></div><details class="disclosure budget-rules"><summary>月均扣繳：所得稅與 ACC</summary><div class="disclosure-body">{deduction_grid(month_paye,month_levy,'月均拆分估算；由雙週換算，各項分別四捨五入，尾數可能差一分。')}</div></details>'''
cost_labels={'rent':'房租','fuel':'油錢','food':'餐費'}
blocks['costs']='<dl class="cost-list">'+''.join(f'<div><dt>{cost_labels.get(key,key)}<small>每週 {money(D(value),True)}</small></dt><dd>{money(D(value)*52/12)}<small>每月</small></dd></div>' for key,value in data['weekly_costs'].items())+f'</dl><p class="muted">每週共 {money(weekly_cost,True)}，按 52÷12 換算每月。未列支出另計。</p>'
blocks['basis']=f'<p class="basis-line">第 {min(w["number"] for w in included)}–{max(w["number"] for w in included)} 週，共 {count} 週平均：排班 <b>{fmt(scheduled_total/count,2)} h</b>／計薪 <b>{fmt(average,2)} h</b>。含第 5 週，只排除到職的第 1 週。</p>'
blocks['actual-summary']=f'''<div class="actual-summary"><div><h3>累計稅後實領</h3><div class="big-number">{money(actual_totals['net'])}</div></div><div><label>累計稅前</label>{money(actual_totals['gross'])}</div></div><div class="card cumulative-deductions">{deduction_grid(actual_totals['paye'],actual_levy,f'{md(day(summary["as_of"]))} 年度稅務摘要實績；所得稅與 ACC 分開列示。')}<p class="muted">累計合計扣繳比例 {fmt(actual_totals['paye']/actual_totals['gross']*100,2)}%；PAYE 包含所得稅與 ACC。</p></div>'''
options=''.join(f'<option value="{key}" {"selected" if key=="planned" else ""}>{label}</option>' for key,label,_,_ in plans)
blocks['refund']=f'''<div class="tax-picker"><label for="workPlan">後續工作安排</label><select id="workPlan" name="workPlan" aria-describedby="refundBasis">{options}</select></div><div class="refund-hero"><div><span id="refundLabel" class="overline">預估退稅</span><div class="refund-number">{money(base['refund'],nzd_id='refundValue',twd_id='refundTwd')}</div></div><p id="refundBasis" class="refund-context">原規劃三份；後續工作尚未確定。尚未含 ASB 實際利息與 RWT。</p></div><p class="notice">續工安排不同，薪資基準退稅由 {money(min(m['refund'] for m in models.values()),True)} 至 {money(max(m['refund'] for m in models.values()),True)}；詳細假設可展開查看。</p>'''
scenario_rows=''.join(f'<li><b>{label}</b><div><div><small>年度稅前</small>{money(models[key]["gross"])}</div><div><small>退稅估值</small>{money(models[key]["refund"])}</div></div></li>' for key,label,_,_ in plans)
sensitivity_rows=''.join(f'<tr><th scope="row">{label}</th><td>{money(m["refund"])}</td></tr>' for label,m in sensitivity)
blocks['refund-detail']=f'''<p class="muted">以下是未計利息的薪資基準情境。</p><ul class="currency-comparison">{scenario_rows}</ul><p class="muted">第二份暫定 2026/11/20–2027/2/19；第三份暫定 2027/3/15 起。同時薪、平均工時、M 稅碼及雙週發薪，均屬假設。</p><h3>所選情境：預扣與全年結算</h3><table class="math-table"><tbody>{table_row('全年應稅薪資',base['gross'],'gross')}{table_row('預扣所得稅',base['prepaid_income_tax'],'prepaid_income_tax')}{table_row('預扣 ACC',base['prepaid_acc'],'prepaid_acc')}{table_row('預扣合計 PAYE',base['paye'],'paye')}{table_row('稅前銀行利息',None,'interest')}{table_row('利息預扣 RWT',None,'rwt')}{table_row('全年應繳所得稅',base['income_tax'],'income_tax')}{table_row('全年應繳 ACC',base['acc'],'acc')}{table_row('預估退稅／補稅',base['refund'],'refund')}</tbody></table><p class="muted">預扣合計＋RWT－全年所得稅－全年 ACC＝退稅或補稅。預扣拆分採稅務摘要實績加未來估值；未來 ACC 按 1.75% 拆估，所得稅取 PAYE 餘額。各雇主分開扣繳，最後以 IRD 結算為準。</p>'''
blocks['sensitivity']=f'<p class="muted">未計利息的薪資基準比較。</p><table><tbody>{sensitivity_rows}</tbody></table><p class="muted">20／32 小時只替換未公布部分，保留實績與已公布班表。週薪比較僅改後兩份工作；數值不是保證範圍。</p>'
blocks['spread']='<div class="spread-grid">'+''.join(f'<article class="spread-card"><h3>攤回 {n} 個月</h3><dl><dt>含退稅月領</dt><dd data-spread-net="{n}">{money(month["net"]+base["refund"]/n)}</dd><dt>月可存</dt><dd data-spread-saving="{n}">{money(month["saving"]+base["refund"]/n)}</dd></dl></article>' for n in [6,9])+'</div><p class="muted">只供平均比較，退稅在年度結算後入帳。稅前月薪不變；9 個月情境未計下一稅年度可能退稅。</p>'
blocks['bank']=f'''<div class="bank-note"><div><span class="overline">已確認帳戶</span><h3>ASB Savings On Call <strong>{fmt(D(data["bank_interest"]["annual_rate"])*100,2)}% p.a.</strong></h3></div><span class="tag estimate">金額待確認</span></div><p class="muted">只估 {md(asof)}–{md(tax_end)}，共 {data['bank_interest']['forecast_days']} 天；假設現行利率維持不變。</p><div class="input-grid"><label for="bankBalance">這段期間的平均存款（NZD）<input id="bankBalance" type="number" min="0" step="0.01" placeholder="填入平均餘額" inputmode="decimal" aria-describedby="interestImpact bankBalanceTwd"><small id="bankBalanceTwd" class="input-twd" data-input-twd-for="bankBalance">台幣參考：尚未填入</small></label><label for="bankRwt">RWT 稅率<select id="bankRwt"><option value="0.105">10.5%</option><option value="0.175" selected>17.5%（暫用假設）</option><option value="0.30">30%</option><option value="0.33">33%</option><option value="0.39">39%</option><option value="0.45">45%</option></select></label></div><div id="interestImpact" class="input-feedback">利息與 RWT 金額未提供，退稅基準先依薪資估算。</div><details class="disclosure"><summary>補入已收利息與已扣 RWT</summary><div class="disclosure-body"><p class="muted">依 ASB 或 myIR 填入同一期間的實際金額。</p><div class="input-grid"><label for="interestIncome">已收稅前利息（NZD）<input id="interestIncome" type="number" min="0" step="0.01" placeholder="稅前金額" inputmode="decimal" aria-describedby="interestImpact interestIncomeTwd"><small id="interestIncomeTwd" class="input-twd" data-input-twd-for="interestIncome">台幣參考：尚未填入</small></label><label for="interestRwt">已扣 RWT（NZD）<input id="interestRwt" type="number" min="0" step="0.01" placeholder="已扣金額" inputmode="decimal" aria-describedby="interestImpact interestRwtTwd"><small id="interestRwtTwd" class="input-twd" data-input-twd-for="interestRwt">台幣參考：尚未填入</small></label></div></div></details><details class="disclosure"><summary>利率、計息與稅務規則</summary><div class="disclosure-body"><p class="muted">按日計息、每月入帳。{fmt(D(data['bank_interest']['annual_rate'])*100,2)}% 自 {data['bank_interest']['effective_at']} 生效，於 {escape(data['bank_interest']['checked_on'])} 查核。每 {money(1000,True)} 平均餘額，一年稅前利息約 {money(D(data['bank_interest']['annual_rate'])*1000,True)}。過去已收利息另補，年度金額以 ASB／myIR 為準。<a href="{escape(data['bank_interest']['source_url'],quote=True)}" target="_blank" rel="noopener noreferrer">ASB 官方利率</a>。</p><p class="muted">居民利息併入所得稅、不計 ACC；RWT 可抵所得稅。邊際稅率與 RWT 都是 17.5% 時，小額利息通常不改變退稅。非稅務居民需另查 NRWT。<a href="https://www.ird.govt.nz/rwt-rate" target="_blank" rel="noopener noreferrer">IRD 扣繳規則</a>。</p></div></details><div class="calculator-actions"><small>只在本頁試算，不儲存輸入。</small><button id="resetInterest" class="text-button" type="button">還原利息試算</button></div>'''
shifts=[]
for s in latest_week['shifts']:
    d=day(s['date'])
    state='預定班次' if d>=asof else '班表日期已過 · 待薪資單'
    shifts.append(f'<li><div class="shift-date"><b>{md(d)}</b><span>週{weekday(d)}</span></div><div><strong>{s["start"]}–{s["end"]}</strong><small>{escape(s.get("role",""))}</small></div><span class="tag estimate">{state}</span></li>')
blocks['roster']=f'''<div class="subhead"><h3>{span(latest_start,latest_end)} 班表</h3><span class="muted">{md(day(data['latest_roster_published']))} 公布</span></div><ul class="shift-list">{''.join(shifts)}</ul><p class="roster-total">排班 <b>{fmt(scheduled[latest_week['number']],2)} h</b>／預估計薪 <b>{fmt(paid[latest_week['number']],2)} h</b></p><p class="muted">每班滿 4 小時預扣 30 分鐘無薪休息；待薪資單核對。</p>'''
payrows=[]
for p in future_paydays:
    period=span(p['period_start'],p['period_end']) if p['end']==p['period_end'] else f'末期至 {md(p["end"])}'
    split=deduction_grid(p['paye'],cent(p['gross']*D(data['acc_rate'])),'預估拆分；合計 PAYE 依 M 公式計算。')
    payrows.append(f'<li><div class="payday-date"><b>{md(p["pay_date"])}（{weekday(p["pay_date"])}）</b><small>{period}</small></div><dl class="payday-values"><div><dt>稅前</dt><dd>{money(p["gross"])}</dd></div><div><dt>稅後</dt><dd>{money(p["net"])}</dd></div></dl><details class="disclosure payday-tax"><summary>所得稅與 ACC 拆分</summary><div class="disclosure-body">{split}</div></details></li>')
pending_note=''.join(f'<p class="muted">目前已公布班表只到 {md(sunson_roster_until)}；{span(p["start"],p["end"])} 尚未收到班表。{md(p["pay_date"])} 可能尾薪暫不估，也未計入退稅；不代表薪資為 0。</p>' for p in pending_paydays)
blocks['paydays']=f'<ul class="payday-list">{"".join(payrows)}</ul><p class="muted">已公布的 Sunson 班次計入相應薪期；尚未公布的班次不視為已排班。</p>{pending_note}'
publications=[]
start=latest_start+timedelta(days=7)
while start<=sunson_end:
    publish_start=start-timedelta(days=2)
    state='預計公布' if publish_start>=asof else '公告日已過，尚未收到'
    publications.append(f'<li><b>{span(publish_start,start-timedelta(days=1))}</b><span>{state} {span(start,start+timedelta(days=6))} 班表</span></li>')
    start+=timedelta(days=7)
blocks['publish']='<ul class="publication-list">'+''.join(publications)+'</ul><p class="muted">最新已收到班表至 11/15；其後的班次未列為已排班，待公布後再更新。</p>'
actual_cards=[]
for i,s in enumerate(sorted(actual,key=lambda s:s['pay_date'],reverse=True)):
    tax,levy=deduction_split(s)
    basis=s.get('deduction_split',{}).get('basis','所得稅／ACC 為拆分估算：ACC 按稅前 ×1.75%，所得稅取 PAYE 餘額；非薪資單逐項明列。')
    split_tag='摘要差額核對' if s.get('deduction_split') else '拆分估算'
    actual_cards.append(f'''<details class="pay-record" {"open" if i==0 else ""}><summary><span><b>{md(day(s['pay_date']))} 發薪</b><small>{span(day(s['period_start']),day(s['period_end']))}</small></span><span class="record-net"><small>稅後實領</small>{money(D(s['net']))}</span></summary><div class="record-body"><dl class="record-grid"><div><dt>計薪</dt><dd>{fmt(D(s['hours']),2)} h</dd></div><div><dt>稅前實績</dt><dd>{money(D(s['gross']))}</dd></div><div><dt>合計扣繳 PAYE · 實績</dt><dd>{money(D(s['paye']))}</dd></div></dl><div class="split-heading"><span class="tag {'actual' if s.get('deduction_split') else 'estimate'}">{split_tag}</span><span>所得稅 {fmt(tax/D(s['gross'])*100,2)}% ＋ ACC {fmt(levy/D(s['gross'])*100,2)}% ≈ 合計 {fmt(D(s['paye'])/D(s['gross'])*100,2)}%</span></div><dl class="deduction-grid split-two"><div><dt>所得稅</dt><dd>{money(tax)}</dd></div><div><dt>ACC 意外傷害保險費</dt><dd>{money(levy)}</dd></div></dl><p class="split-note">{escape(basis)}</p></div></details>''')
blocks['actual']='<div class="pay-records">'+''.join(actual_cards)+'</div>'
blocks['tax-proof']=f'<p class="muted">{len(summary_slips)} 期薪資與 {md(day(summary["as_of"]))} 稅務摘要相符，以下皆為累計實績。</p><table><tbody>'+''.join(table_row(label,D(summary[key])) for label,key in [('稅前薪資','taxable_earnings'),('所得稅','income_tax_deductions'),('ACC','acc_earners_levy'),('合計扣繳 PAYE','total_deductions')])+'</tbody></table>'
last_tax,last_levy=deduction_split(last_slip)
blocks['withholding-guide']=f'''<div class="tax-explainer"><p><strong>PAYE＝所得稅＋ACC。</strong>所得稅按收入級距計算；ACC 是意外傷害保險費，本稅年費率為 1.75%。</p><h3>為什麼每期比例不同？</h3><p>佳蓉使用 M 稅碼、雙週給薪。每次先將當期稅前 ×26 換算成年收入，計算所得稅與 ACC，再換回這兩週應扣金額。當期薪資不同，平均扣繳比例就不同。</p><div class="tax-bands"><div><span>雙週前 {money(600,True)}</span><b>12.25%</b><small>所得稅 10.5%＋ACC 1.75%</small></div><div><span>超過上述金額的部分</span><b>19.25%</b><small>所得稅 17.5%＋ACC 1.75%</small></div></div><p class="muted">這是目前薪資範圍的簡化對照，適用雙週稅前不超過約 {money(D(53500)/26,True)}；更高收入另有級距。雇主實際計算還有年化截元與按週截分，可能出現幾分差。</p><h3>{md(day(last_slip['pay_date']))} 這期怎麼看</h3><p>稅前 {money(D(last_slip['gross']),True)}，合計扣繳 {money(D(last_slip['paye']),True)}，比例為 <strong>{fmt(D(last_slip['paye'])/D(last_slip['gross'])*100,2)}%</strong>；其中所得稅 {money(last_tax,True)}、ACC {money(last_levy,True)}。</p><p class="muted">所得稅與 ACC 的來源見該期標籤。當期扣繳按當期薪資年化；年度所得稅則按整個稅年的實際總收入結算，因此目前預扣比例不等於最後全年稅負。</p></div>'''
covered_numbers={n for s in actual for n in s['weeks']}
week_cards=[]
for w in reversed(weeks):
    n=w['number']; start=day(w['start']); groups=OrderedDict()
    for s in w['shifts']:
        groups.setdefault((s['start'],s['end']),[]).append(md(day(s['date'])))
    rows=''.join(f'<li><span>{"、".join(ds)}</span><b>{a}–{b}</b></li>' for (a,b),ds in groups.items())
    state='薪期拆估' if n in covered_numbers and n!=1 else '薪資單' if n==1 else '班表估計'
    note='本週不列入平均。' if n in data['excluded_weeks'] else ''
    week_cards.append(f'<details class="week-record"><summary><span><b>第 {n} 週 · {span(start,start+timedelta(days=6))}</b><small>排班 {fmt(scheduled[n],2)} h · 計薪約 {fmt(paid[n],2)} h · {state}</small></span></summary><ul class="week-shifts">{rows}</ul>{f"<p class=muted>{note}</p>" if note else ""}</details>')
blocks['weeks']='<div class="week-records">'+''.join(week_cards)+f'</div><p class="muted">平均採第 {min(w["number"] for w in included)}–{max(w["number"] for w in included)} 週，排班共 {fmt(scheduled_total,2)} h、計薪共 {fmt(paid_total,2)} h。已領薪週按薪期實績比例拆分，單週仍屬估值；未領薪週才扣預設休息。</p>'
blocks['method']=f'''<div class="method-grid"><div><h3>薪資與預算</h3><ul><li>累計前 20 小時按 {money(D(data['first_20_hour_rate']),True)}／小時，其後 {money(D(data['hourly_rate']),True)}／小時；另加 8% 假期加給，已計入各期稅前。</li><li>未領薪每班滿 4 小時扣 30 分鐘；薪資單時數保留實績，不再扣一次。</li><li>一般月薪按雙週 ×26÷12；生活費每週 {money(weekly_cost,True)}，月化 ×52÷12，明細見收支區。</li></ul></div><div><h3>退稅的年度與限制</h3><ul><li>只計 2026/4/1–2027/3/31 入帳薪資。依假設薪期，3/26 領 3/15–21；3/22 後若 4/9 領，歸下一稅年。</li><li>M 稅碼依 IRD 截尾規則；普通工資與 8% 加給分別取分。公式已重現 {len(actual)} 期實際 PAYE。</li><li>ACC 1.75%，本稅年計費薪資上限 {money(D(data['acc_cap']),True)}；利息不課 ACC。現有薪資情境皆未達 IETC 最低年度所得 {money(24000,True)}，未加所得稅抵免。</li><li>未提供的其他收入、境外收入、居住身分與抵免可能影響結算；後續工作與薪期確定後須重算。</li></ul></div></div>'''
blocks['sources']='''<ul class="source-list"><li><a href="https://www.ird.govt.nz/-/media/project/ir/home/documents/digital-service-providers/software-providers/payroll-calculations-business-rules-specifications/payroll-calculations-and-business-rules-specification.pdf" target="_blank" rel="noopener noreferrer">IRD 2026/27 PAYE 公式 §5.2</a></li><li><a href="https://www.ird.govt.nz/income-tax/income-tax-for-individuals/tax-codes-and-tax-rates-for-individuals/tax-rates-for-individuals" target="_blank" rel="noopener noreferrer">所得稅級距</a> · <a href="https://www.ird.govt.nz/income-tax/income-tax-for-individuals/acc-clients-and-carers/acc-earners-levy-rates" target="_blank" rel="noopener noreferrer">ACC 費率</a></li><li><a href="https://www.ird.govt.nz/income-tax/income-tax-for-individuals/individual-tax-credits/independent-earner-tax-credit-ietc" target="_blank" rel="noopener noreferrer">IETC 資格</a> · <a href="https://www.ird.govt.nz/international-tax/individuals/tax-residency-status-for-individuals" target="_blank" rel="noopener noreferrer">稅務居民</a></li><li><a href="https://www.employment.govt.nz/pay-and-hours/pay-and-wages/final-pay" target="_blank" rel="noopener noreferrer">尾薪時程</a> · <a href="https://www.taxtechnical.ird.govt.nz/operational-positions/commissioners-operational-position-on-calculating-paye-on-holiday-pay" target="_blank" rel="noopener noreferrer">假期薪資扣繳</a></li><li><a href="https://www.ird.govt.nz/-/media/project/ir/home/documents/forms-and-guides/ir300---ir399/ir335/ir335.pdf" target="_blank" rel="noopener noreferrer">IRD 雇主指南（部分薪期）</a> · <a href="https://www.taxpolicy.ird.govt.nz/-/media/project/ir/tp/publications/2018/2018-other-mts-cabinet-papers/2018-other-mts-cabinet-papers-3-1-paye-pdf.pdf" target="_blank" rel="noopener noreferrer">薪資收現年度</a></li></ul>'''
blocks['freshness']=f'<span>實薪至 <b>{md(day(last_slip["pay_date"]))}</b></span><span>班表公布 <b>{md(day(data["latest_roster_published"]))}</b></span>'
blocks['currency-basis']=f'<a href="#sources" id="currencyBasis">台幣依星展買入 {data["fx"]["buy_rate"]} 換算 · {escape(data["fx"]["quoted_at"])}</a>'
blocks['fx']=f'''<div class="fx-heading"><label for="fxRate">紐幣換回台幣</label><span class="rate-input">NZ$1 = NT$ <input id="fxRate" type="number" min="0.01" max="1000" step="0.01" value="{data['fx']['buy_rate']}" inputmode="decimal" aria-describedby="fxHelp fxStatus"></span></div><div class="fx-controls"><p id="fxStatus" class="fx-status" role="status">使用星展牌告買入 {data['fx']['buy_rate']}</p><button id="resetFx" class="text-button" type="button">還原牌告</button></div><p id="fxHelp"><a href="{escape(data['fx']['source_url'],quote=True)}" target="_blank" rel="noopener noreferrer">星展即期買入 {data['fx']['buy_rate']}</a> · 同時賣出 {data['fx']['sell_rate']}<br>牌告 {escape(data['fx']['quoted_at'])}；{escape(data['fx'].get('checked_on',data['updated']).replace('-','/'))} 查核。實際成交仍依銀行報價。</p>'''
blocks['currency-details']='<p class="muted">全站金額旁已直接附台幣參考，依上方買入牌告或自訂匯率同步換算。台幣以整元顯示、各項獨立取整；不代表實際入帳金額。匯率為有日期的快照，非即時報價。</p>'
client_models={k:{field:value for field,value in m.items() if field!='future'} for k,m in models.items()}
client={'scenarios':client_models,'monthly':month,'defaultPlan':'planned','interest':data['bank_interest'],'fx':data['fx'],'updated':data['updated']}
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
