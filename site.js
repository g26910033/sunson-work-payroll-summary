(() => {
  'use strict';
  const node = document.getElementById('forecastModel');
  if (!node) return;
  const model = JSON.parse(node.textContent);
  const byId = (id) => document.getElementById(id);
  const integer = new Intl.NumberFormat('zh-TW', { maximumFractionDigits: 0 });
  const decimal = new Intl.NumberFormat('zh-TW', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const cents = (n) => Math.round((Number(n) + Number.EPSILON) * 100);
  const money = (n) => cents(n) / 100;
  const nzd = (n, precise = false) => 'NZ$' + (precise ? decimal : integer).format(n);
  const parsedInput = (id) => {
    const input = byId(id);
    if (!input || input.value.trim() === '') return null;
    const n = Number(input.value);
    return Number.isFinite(n) && n >= 0 ? n : null;
  };
  // 以分和整數稅率運算，避免年度所得稅遇半分時的浮點誤差。
  function taxInCents(incomeCents) {
    let total = 0, lower = 0;
    for (const [upper, rate] of [[1560000,1050],[5350000,1750],[7810000,3000],[18000000,3300],[Infinity,3900]]) {
      total += Math.max(0, Math.min(incomeCents, upper) - lower) * rate;
      lower = upper;
    }
    return Math.floor((total + 5000) / 10000);
  }
  function updateCurrency() {
    const rate = Number(byId('fxRate').value);
    if (!Number.isFinite(rate) || rate <= 0) return;
    document.querySelectorAll('[data-nzd-to-twd]').forEach((element) => {
      const amount = Number(element.dataset.nzdToTwd);
      if (Number.isFinite(amount)) element.textContent = '約 NT$' + integer.format(Math.round(amount * rate));
    });
  }
  function updateTax() {
    const key = document.querySelector('input[name="workPlan"]:checked')?.value || model.defaultPlan;
    const plan = model.scenarios[key];
    const balance = parsedInput('bankBalance');
    const pastInterest = parsedInput('interestIncome');
    const pastRwt = parsedInput('interestRwt');
    const pastIncluded = pastInterest !== null && pastRwt !== null;
    const futureIncluded = balance !== null;
    const futureInterest = futureIncluded ? money(balance * Number(model.interest.annual_rate) * Number(model.interest.forecast_days) / 365) : 0;
    const futureRwt = money(futureInterest * Number(byId('bankRwt').value));
    const interest = money((pastIncluded ? pastInterest : 0) + futureInterest);
    const rwt = money((pastIncluded ? pastRwt : 0) + futureRwt);
    const included = pastIncluded || futureIncluded;
    const incomeTax = taxInCents(cents(plan.gross) + cents(interest)) / 100;
    const refund = money(Number(plan.paye) + rwt - incomeTax - Number(plan.acc));
    byId('refundValue').textContent = (refund < 0 ? '應補 ' : '') + nzd(Math.abs(refund));
    byId('refundTwd').dataset.nzdToTwd = refund.toFixed(2);
    byId('refundBasis').textContent = plan.label + '；' + (key === 'sunson' ? '假設其後不再領薪。' : '後續工作仍待確認。') + (included ? '加計已輸入的利息情境；未填部分未計。' : '尚未含 ASB 實際利息與 RWT。');
    const values = { gross:Number(plan.gross), paye:Number(plan.paye), income_tax:incomeTax, acc:Number(plan.acc), refund };
    document.querySelectorAll('[data-tax-field]').forEach((element) => {
      const field = element.dataset.taxField;
      element.textContent = field === 'interest' ? (included ? nzd(interest,true) + ' ／ RWT ' + nzd(rwt,true) : '金額未提供；基準未計') : nzd(values[field],true);
    });
    document.querySelectorAll('[data-spread-net]').forEach((element) => element.textContent = nzd(Number(model.monthly.net) + refund / Number(element.dataset.spreadNet)));
    document.querySelectorAll('[data-spread-saving]').forEach((element) => element.textContent = nzd(Number(model.monthly.saving) + refund / Number(element.dataset.spreadSaving)));
    document.querySelectorAll('[data-spread-net-twd]').forEach((element) => element.dataset.nzdToTwd = money(Number(model.monthly.net) + refund / Number(element.dataset.spreadNetTwd)));
    document.querySelectorAll('[data-spread-saving-twd]').forEach((element) => element.dataset.nzdToTwd = money(Number(model.monthly.saving) + refund / Number(element.dataset.spreadSavingTwd)));
    let note = 'ASB Savings On Call 現行年利率 ' + (Number(model.interest.annual_rate)*100).toFixed(2) + '%；利息與 RWT 金額尚未納入。';
    if (included) {
      const difference = money(refund - Number(plan.refund));
      note = (futureIncluded ? '未來 ' + model.interest.forecast_days + ' 天利息約 ' + nzd(futureInterest,true) + '、RWT ' + nzd(futureRwt,true) + '。' : '') + '所填利息使退稅' + (difference >= 0 ? '增加 ' : '減少 ') + nzd(Math.abs(difference),true) + '。';
      if (!pastIncluded) note += '截至目前的利息實績仍未計入。';
    }
    if ((pastInterest === null) !== (pastRwt === null)) note += '請一起填已收利息與已扣 RWT，才能計入實績。';
    const annualIncome = Number(plan.gross) + interest;
    if (annualIncome >= 24000 && annualIncome < 70000) note += '全年所得已達 IETC 所得範圍，仍須確認居民身分等資格；此處尚未加抵免。';
    if (annualIncome >= 70000) note += '全年所得已達 IETC 上限，此處不加該抵免。';
    byId('interestImpact').textContent = note;
    byId('taxStatus').textContent = plan.label + '，' + (refund < 0 ? '預估補稅 ' : '預估退稅 ') + nzd(Math.abs(refund)) + '。';
    updateCurrency();
  }
  document.querySelectorAll('input[name="workPlan"]').forEach((input) => input.addEventListener('change', updateTax));
  ['bankBalance','bankRwt','interestIncome','interestRwt'].forEach((id) => byId(id)?.addEventListener('input',updateTax));
  byId('fxRate').addEventListener('input',updateCurrency);
  byId('shareButton').addEventListener('click',async () => {
    try {
      await navigator.clipboard.writeText('https://gpt.greenparty.org.tw/sunson-work-payroll-summary/');
      byId('shareButton').textContent = '已複製網址';
      byId('shareStatus').textContent = '網站網址已複製。';
    } catch {
      byId('shareStatus').classList.remove('sr-only');
      byId('shareStatus').textContent = '請直接複製瀏覽器的網站網址。';
    }
  });
  let printState = [];
  addEventListener('beforeprint',() => {
    printState = [...document.querySelectorAll('details')].map((element) => [element,element.open]);
    printState.forEach(([element]) => {element.open = true;});
  });
  addEventListener('afterprint',() => {printState.forEach(([element,open]) => {element.open = open;});});
  if (model.interest.annual_gross !== null && model.interest.annual_rwt !== null) {
    byId('interestIncome').value = model.interest.annual_gross;
    byId('interestRwt').value = model.interest.annual_rwt;
  }
  updateTax();
})();
