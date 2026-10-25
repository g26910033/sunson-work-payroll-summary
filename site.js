(() => {
  'use strict';
  const node = document.getElementById('forecastModel');
  if (!node) return;
  const model = JSON.parse(node.textContent);
  const byId = (id) => document.getElementById(id);
  const integer = new Intl.NumberFormat('zh-TW', { maximumFractionDigits: 0 });
  const decimal = new Intl.NumberFormat('zh-TW', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const rateFormat = new Intl.NumberFormat('zh-TW', { minimumFractionDigits: 2, maximumFractionDigits: 4 });
  const cents = (n) => Math.round((Number(n) + Number.EPSILON) * 100);
  const money = (n) => cents(n) / 100;
  const nzd = (n, precise = false) => 'NZ$' + (precise ? decimal : integer).format(n);
  let announcement;

  function parsedInput(id) {
    const input = byId(id);
    if (!input) return null;
    const blank = input.value.trim() === '';
    const n = Number(input.value);
    const invalid = input.validity.badInput || (!blank && (!Number.isFinite(n) || n < 0 || n > 1000000000));
    input.setAttribute('aria-invalid', String(invalid));
    return blank || invalid ? null : n;
  }

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
    const input = byId('fxRate');
    const rate = Number(input.value);
    const valid = input.value.trim() !== '' && Number.isFinite(rate) && rate >= 0.01 && rate <= 1000;
    input.setAttribute('aria-invalid', String(!valid));
    const status = byId('fxStatus');
    status.classList.toggle('error', !valid);
    const statusText = !valid ? '請輸入 0.01–1000 的匯率；台幣換算暫不顯示。' :
      Math.abs(rate - Number(model.fx.buy_rate)) < 0.0000001 ? '使用星展牌告買入 ' + rateFormat.format(rate) :
      '目前使用自訂匯率 ' + rateFormat.format(rate) + '，僅供試算。';
    if (status.textContent !== statusText) status.textContent = statusText;
    document.querySelectorAll('[data-nzd-to-twd]').forEach((element) => {
      const amount = Number(element.dataset.nzdToTwd);
      if (Number.isFinite(amount)) element.textContent = valid ? '約 NT$' + integer.format(Math.round(amount * rate)) : '—';
    });
  }

  function updateTax(announce = true) {
    const key = byId('workPlan').value || model.defaultPlan;
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
    const taxLabel = refund < 0 ? '預估補稅' : '預估退稅';
    byId('refundLabel').textContent = taxLabel;
    byId('refundValue').textContent = nzd(Math.abs(refund), true);
    byId('refundTwd').dataset.nzdToTwd = Math.abs(refund).toFixed(2);
    byId('refundBasis').textContent = plan.label + '；' + (key === 'sunson' ? '假設 11/19 後不再領薪。' : '後續工作仍待確認。') +
      (included ? '已加計所填利息，未填部分未計。' : '尚未含 ASB 實際利息與 RWT。');
    const values = { gross:Number(plan.gross), paye:Number(plan.paye), income_tax:incomeTax, acc:Number(plan.acc), refund };
    document.querySelectorAll('[data-tax-field]').forEach((element) => {
      const field = element.dataset.taxField;
      element.textContent = field === 'interest' ? (included ? nzd(interest,true) + ' ／ RWT ' + nzd(rwt,true) : '金額未提供；基準未計') : nzd(values[field],true);
    });
    document.querySelectorAll('[data-spread-net]').forEach((element) => element.textContent = nzd(Number(model.monthly.net) + refund / Number(element.dataset.spreadNet)));
    document.querySelectorAll('[data-spread-saving]').forEach((element) => element.textContent = nzd(Number(model.monthly.saving) + refund / Number(element.dataset.spreadSaving)));
    document.querySelectorAll('[data-spread-net-twd]').forEach((element) => element.dataset.nzdToTwd = money(Number(model.monthly.net) + refund / Number(element.dataset.spreadNetTwd)));
    document.querySelectorAll('[data-spread-saving-twd]').forEach((element) => element.dataset.nzdToTwd = money(Number(model.monthly.saving) + refund / Number(element.dataset.spreadSavingTwd)));

    let note = '利息與 RWT 金額未提供，退稅基準先依薪資估算。';
    if (included) {
      const difference = money(refund - Number(plan.refund));
      note = (futureIncluded ? '未來利息約 ' + nzd(futureInterest,true) + '，RWT ' + nzd(futureRwt,true) + '。' : '') +
        '退稅' + (difference >= 0 ? '增加 ' : '減少 ') + nzd(Math.abs(difference),true) + '。';
      if (!pastIncluded) note += '過去利息實績尚未計入。';
    }
    if ((pastInterest === null) !== (pastRwt === null)) note += '請一起填入已收利息與已扣 RWT。';
    const invalid = ['bankBalance','interestIncome','interestRwt'].some((id) => byId(id).getAttribute('aria-invalid') === 'true');
    if (invalid) note = '請檢查標示欄位，金額須為 0–10 億；無效欄位暫不納入。' + note;
    byId('interestImpact').classList.toggle('error', invalid);
    const annualIncome = Number(plan.gross) + interest;
    if (annualIncome >= 24000 && annualIncome < 70000) note += '年度所得已達 IETC 範圍，仍須確認資格；此處未加抵免。';
    if (annualIncome >= 70000) note += '年度所得已達 IETC 上限，此處不加該抵免。';
    byId('interestImpact').textContent = note;
    clearTimeout(announcement);
    if (announce) announcement = setTimeout(() => {
      const message = plan.label + '：' + taxLabel + ' ' + nzd(Math.abs(refund),true) + '。';
      if (byId('taxStatus').textContent !== message) byId('taxStatus').textContent = message;
    }, 400);
    updateCurrency();
  }

  function restoreInterest() {
    byId('bankBalance').value = '';
    byId('bankRwt').value = '0.175';
    byId('interestIncome').value = model.interest.annual_gross ?? '';
    byId('interestRwt').value = model.interest.annual_rwt ?? '';
  }
  byId('workPlan').addEventListener('change', () => updateTax());
  ['bankBalance','bankRwt','interestIncome','interestRwt'].forEach((id) => byId(id).addEventListener('input', () => updateTax()));
  byId('fxRate').addEventListener('input', updateCurrency);
  byId('resetFx').addEventListener('click', () => { byId('fxRate').value = model.fx.buy_rate; updateCurrency(); });
  byId('resetInterest').addEventListener('click', () => { restoreInterest(); updateTax(); });

  let shareTimer;
  byId('shareButton').addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText('https://gpt.greenparty.org.tw/sunson-work-payroll-summary/');
      byId('shareButton').textContent = '已複製網址';
      byId('shareStatus').textContent = '網站網址已複製。';
      clearTimeout(shareTimer);
      shareTimer = setTimeout(() => { byId('shareButton').textContent = '分享網址 ↗'; }, 2500);
    } catch {
      byId('shareStatus').classList.remove('sr-only');
      byId('shareStatus').textContent = '請直接複製瀏覽器的網站網址。';
    }
  });

  const navLinks = [...document.querySelectorAll('.section-nav a')];
  let navFrame;
  function updateNavigation() {
    navFrame = undefined;
    let current = navLinks[0];
    navLinks.forEach((link) => {
      const target = document.querySelector(link.getAttribute('href'));
      if (target && target.getBoundingClientRect().top <= 115) current = link;
    });
    navLinks.forEach((link) => link === current ? link.setAttribute('aria-current','location') : link.removeAttribute('aria-current'));
  }
  addEventListener('scroll', () => { if (!navFrame) navFrame = requestAnimationFrame(updateNavigation); }, {passive:true});
  addEventListener('resize', updateNavigation);

  let printState = [];
  addEventListener('beforeprint', () => {
    printState = [...document.querySelectorAll('details')].map((element) => [element,element.open]);
    printState.forEach(([element]) => {element.open = true;});
  });
  addEventListener('afterprint', () => {printState.forEach(([element,open]) => {element.open = open;});});
  restoreInterest();
  updateTax(false);
  updateNavigation();
})();
