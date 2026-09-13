'use strict';
// Workspace presentation enhancements; existing application handlers remain authoritative.
(() => {
  const headerActions = document.querySelector('.header-actions');
  const strip = $('#gmail-strip');
  const gmailButton = $('#open-gmail');
  headerActions.append(gmailButton, strip);
  const changeAccount = document.createElement('button');
  changeAccount.id = 'change-account';
  changeAccount.type = 'button';
  changeAccount.className = 'account-change';
  changeAccount.textContent = 'Change account';
  headerActions.append(changeAccount);
  changeAccount.addEventListener('click', async () => {
    $('#gmail-dialog').showModal();
    await gmailPost('/api/gmail/connect', {});
  });
  $('#new-email').hidden = true;
  document.querySelector('.side-note .pulse')?.remove();
  const profile = document.querySelector('.profile');
  profile.tabIndex = 0;
  profile.setAttribute('role', 'button');
  profile.setAttribute('aria-label', 'Personal inbox — model settings');
  profile.addEventListener('click', () => navigate('models'));
  profile.addEventListener('keydown', event => {
    if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); navigate('models'); }
  });
  const icons = {
    'nav-mail':'<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 6 9 7 9-7"/>',
    'nav-calendar':'<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M7 3v4m10-4v4M3 11h18M7 15h2m4 0h2"/>',
    'nav-memory':'<path d="M4 6h16M4 12h16M4 18h16"/><circle cx="9" cy="6" r="2"/><circle cx="16" cy="12" r="2"/><circle cx="8" cy="18" r="2"/>',
    'nav-models':'<rect x="6" y="6" width="12" height="12" rx="3"/><path d="M9 1v5m6-5v5M9 18v5m6-5v5M1 9h5m-5 6h5m12-6h5m-5 6h5"/><path d="M10 10h4v4h-4z"/>',
    'nav-labels':'<path d="M3 3h8l10 10-8 8L3 11Z"/><circle cx="7.5" cy="7.5" r="1"/>',
    'nav-special':'<path d="m13 2-9 12h7l-1 8 10-13h-7z"/>',
    'nav-autosent':'<path d="m3 3 18 9-18 9 4-9-4-9Zm4 9h14"/>'
  };
  Object.entries(icons).forEach(([id, paths]) => {
    const icon = document.querySelector(`#${id}>span:first-child`);
    if (icon) icon.innerHTML = `<svg viewBox="0 0 24 24" width="19" height="19" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths}</svg>`;
  });
  const reviewCount = $('#label-review-count');
  const updateReviewCount = () => reviewCount.classList.toggle('has-reviews', Number(reviewCount.textContent) > 0);
  new MutationObserver(updateReviewCount).observe(reviewCount, {childList:true, characterData:true, subtree:true});
  updateReviewCount();

  // A pointer must both start and finish outside the content to dismiss a modal.
  // This avoids closing a dialog when a text-selection drag ends on its backdrop.
  let backdropDialog = null;
  const outside = (dialog, event) => { const rect = dialog.getBoundingClientRect(); return event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom; };
  document.addEventListener('pointerdown', event => { backdropDialog = event.target instanceof HTMLDialogElement && outside(event.target, event) ? event.target : null; });
  document.addEventListener('click', event => { if (event.target === backdropDialog && outside(backdropDialog, event)) backdropDialog.close(); backdropDialog = null; });
  document.addEventListener('keydown', event => {
    if (event.key !== 'Escape') return;
    const dialogs = [...document.querySelectorAll('dialog[open]')];
    if (dialogs.length) { event.preventDefault(); dialogs.at(-1).close(); }
  }, true);

  const updateSlider = () => {
    const slider = $('#model-concurrency');
    const fraction = (Number(slider.value) - Number(slider.min)) / (Number(slider.max) - Number(slider.min));
    slider.style.setProperty('--range-progress', `${fraction * 100}%`);
    slider.style.setProperty('--range-offset', `${12 - 24 * fraction}px`);
  };
  const originalModelControls = updateModelControls;
  updateModelControls = function () { originalModelControls(); updateSlider(); };
  $('#model-concurrency').addEventListener('input', updateSlider);
  document.querySelectorAll('input[name="model-mode"]').forEach(input => input.addEventListener('change', updateSlider));

  const originalGmailRender = renderGmail;
  renderGmail = function () {
    originalGmailRender();
    const connection = state?.gmail_connection;
    const account = connection?.account || '';
    const initial = account ? account.charAt(0).toUpperCase() : 'U';
    document.querySelectorAll('.profile .avatar, .gmail-icon').forEach(node => { node.textContent = initial; });
    document.querySelector('.profile small').textContent = account || 'No account connected';
    changeAccount.hidden = !account || connection?.status !== 'connected';
    $('#gmail-auto-sync').closest('.sync-toggle').hidden = true;
    const provider = modelConfig().label || 'Bundled free Groq';
    if (state?.mode !== 'scripted') {
      $('#mode').textContent = provider;
      $('#mode-note').textContent = connection?.live
        ? 'New mail is checked every 10 seconds. Sending follows your approval and Superpowers settings.'
        : 'Gmail actions are unavailable in this running session.';
      if (!account) $('#mode-note').textContent = 'Connect Gmail to start receiving new email.';
    }
    if (connection?.status === 'connected' && !connection.operation) {
      $('#gmail-summary-note').textContent = connection.live
        ? 'Connected · new mail every 10 seconds'
        : 'Connected · Gmail actions unavailable';
    }
  };
  const durations = {last30:'~3 min', last100:'~10 min', all:'~^^~', new:'~instant'};
  document.querySelectorAll('.history-options label').forEach(label => {
    const hint = document.createElement('small'); hint.className = 'history-estimate';
    hint.textContent = durations[label.querySelector('input').value]; label.append(hint);
  });

  $('#calendar-today').textContent = 'Current month';
  const title = $('#calendar-month');
  title.tabIndex = 0; title.setAttribute('role', 'button'); title.setAttribute('aria-label', 'Choose month and year'); title.setAttribute('aria-haspopup', 'dialog');
  const picker = document.createElement('dialog'); picker.id = 'calendar-picker';
  picker.setAttribute('aria-labelledby', 'calendar-picker-title');
  picker.innerHTML = '<div class="dialog-heading"><h2 id="calendar-picker-title">Go to month</h2><button type="button" class="icon-button" aria-label="Close month picker">×</button></div><label class="picker-year-label">Year<select id="calendar-picker-year" aria-label="Year"></select></label><div class="picker-months"></div>';
  document.body.append(picker);
  picker.querySelector('button').addEventListener('click', () => picker.close());
  function drawPicker() {
    const year = Number($('#calendar-picker-year').value), now = new Date();
    const min = new Date(now.getFullYear()-1, now.getMonth(), 1), max = new Date(now.getFullYear()+3, now.getMonth(), 1);
    picker.querySelector('.picker-months').innerHTML = Array.from({length:12}, (_,month) => {
      const date = new Date(year,month,1), selected = year === calendarCursor.getFullYear() && month === calendarCursor.getMonth();
      return `<button type="button" data-month="${month}" ${date < min || date > max ? 'disabled' : ''} class="${selected ? 'selected' : ''}" aria-pressed="${selected}">${date.toLocaleDateString('en-US',{month:'short'})}</button>`;
    }).join('');
  }
  function openPicker() {
    const year = new Date().getFullYear();
    $('#calendar-picker-year').innerHTML = Array.from({length:5},(_,i) => `<option value="${year-1+i}" ${year-1+i === calendarCursor.getFullYear() ? 'selected' : ''}>${year-1+i}</option>`).join('');
    drawPicker(); picker.showModal();
  }
  title.addEventListener('click', openPicker);
  title.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); openPicker(); } });
  $('#calendar-picker-year').addEventListener('change', drawPicker);
  picker.querySelector('.picker-months').addEventListener('click', event => {
    const button = event.target.closest('[data-month]'); if (!button || button.disabled) return;
    calendarCursor = new Date(Number($('#calendar-picker-year').value), Number(button.dataset.month), 1); renderCalendar(); picker.close();
  });
  let lastCalendarMonth = calendarCursor.getTime();
  const originalCalendarRender = renderCalendar;
  renderCalendar = function () {
    originalCalendarRender();
    const now = new Date(), current = calendarCursor.getTime();
    $('#calendar-today').classList.toggle('current-month-hidden', calendarCursor.getMonth() === now.getMonth() && calendarCursor.getFullYear() === now.getFullYear());
    if (current !== lastCalendarMonth && !matchMedia('(prefers-reduced-motion: reduce)').matches) {
      $('#calendar-grid').animate([{opacity:.35,transform:`translateX(${current > lastCalendarMonth ? 18 : -18}px)`},{opacity:1,transform:'translateX(0)'}],{duration:220,easing:'ease-out'});
    }
    lastCalendarMonth = current;
  };
  $('#refresh').addEventListener('click', () => { if (!matchMedia('(prefers-reduced-motion: reduce)').matches) $('#refresh').animate([{transform:'rotate(0deg)'},{transform:'rotate(360deg)'}], {duration:550,easing:'ease-in-out'}); });
  updateSlider(); if (state) { renderGmail(); renderCalendar(); }
})();
