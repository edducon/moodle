window.MoodleBot.activeDeadlines = [];

function spinCircle() {
    const logo = document.querySelector('#moodle-bot-btn img');
    if (!logo) return;
    logo.classList.remove('spin-once');
    void logo.offsetWidth;
    logo.classList.add('spin-once');
}

// === ФУНКЦИИ ДЛЯ ДЕДЛАЙНОВ ===
function getDaysLeft(date) {
    if (!date) return 0;
    const today = new Date(new Date().setHours(0, 0, 0, 0));
    const target = new Date(date.getFullYear(), date.getMonth(), date.getDate());
    return Math.ceil((target - today) / (1000 * 60 * 60 * 24));
}

function buildDeadlineLabel(daysLeft) {
    if (daysLeft < 0) return 'Просрочено';
    if (daysLeft === 0) return 'Сегодня!';
    if (daysLeft === 1) return 'Завтра';
    return daysLeft <= 3 ? `Осталось ${daysLeft} дн.` : `${daysLeft} дней`;
}

async function extractDeadlinesFromCourse() {
    const doms = await getCourseDOMs();
    const assignments = [];
    const seenAssigns = new Set();

    // 1. Быстро собираем все ссылки на задания
    for (const doc of doms) {
        const assigns = doc.querySelectorAll('li.activity.modtype_assign');
        for (const act of assigns) {
            if (isActivityHidden(act) || seenAssigns.has(act.id)) continue;
            seenAssigns.add(act.id);
            const a = act.querySelector('a.aalink');
            if (a) assignments.push({ id: act.id, title: extractModuleTitle(act), url: a.href });
        }
    }

    const deadlines = [];

    // 2. Асинхронно скачиваем страницы пачками по 4 штуки
    for (let i = 0; i < assignments.length; i += 4) {
        const chunk = assignments.slice(i, i + 4);
        const promises = chunk.map(async (assign) => {
            try {
                const response = await fetch(assign.url, { credentials: 'include' });
                const html = await response.text();
                const assignDoc = new DOMParser().parseFromString(html, "text/html");

                const statusEl = assignDoc.querySelector('.submissionstatus, .submissionstatussubmitted, .badge-success');
                if (statusEl && /выполнено|отправлено|submitted/i.test(statusEl.innerText)) return;

                const dueTextMatch = assignDoc.body.innerText.match(/Срок сдачи:\s*(?:[а-яё]+,\s*)?(\d{1,2}\s+[а-яё]+ \d{4})/i);
                if (dueTextMatch && dueTextMatch[1]) {
                    const dueDate = parseRuDate(dueTextMatch[1]);
                    if (dueDate) {
                        deadlines.push({ title: assign.title, due_date: dueDate, url: assign.url, moodle_id: assign.id });
                    }
                }
            } catch (e) {}
        });
        await Promise.all(promises);
    }

    const formattedDeadlines = deadlines.map(d => ({
        title: d.title,
        due_date: d.due_date.toLocaleDateString('ru-RU'),
        url: d.url,
        moodle_id: d.moodle_id
    }));

    window.MoodleBot.activeDeadlines = formattedDeadlines;
    return formattedDeadlines;
}

function renderUngradedWidget(ungradedList) {
    let widget = document.getElementById('moodle-deadlines-widget');
    if (widget) widget.remove();

    widget = document.createElement('div');
    widget.id = 'moodle-deadlines-widget';

    const storageKey = `moodle_ungraded_state_${getCourseId()}`;
    const isCollapsed = sessionStorage.getItem(storageKey) === 'collapsed';
    if (isCollapsed) {
        widget.classList.add('is-collapsed');
    }

    const header = document.createElement('div');
    header.id = 'moodle-deadlines-header';
    header.title = "Нажмите, чтобы развернуть/свернуть";

    const totalCount = (ungradedList || []).reduce((sum, item) => sum + item.count, 0);

    const badgeHtml = totalCount > 0
        ? `<span style="position: absolute; top: -6px; right: -8px; background: #dc3545; color: white; font-size: 10px; font-weight: bold; padding: 2px 5px; border-radius: 10px; line-height: 1; border: 2px solid #343a40; z-index: 2;" title="Ждут проверки">${totalCount}</span>`
        : `<span style="position: absolute; top: -4px; right: -6px; background: #28a745; color: white; font-size: 9px; font-weight: bold; padding: 2px 4px; border-radius: 10px; line-height: 1; border: 2px solid #343a40; z-index: 2;" title="Всё проверено">✓</span>`;

    header.innerHTML = `
        <div style="position: relative; display: inline-flex; align-items: center; justify-content: center; margin-right: 12px; margin-left: 5px;">
            <span class="md-icon" style="margin-right: 0; font-size: 16px;">📝</span>
            ${badgeHtml}
        </div>
        <span class="md-title" style="flex-grow: 1;">Требуют оценки</span>
        <button class="md-refresh-btn" title="Принудительно обновить" style="margin-left: 10px; margin-right: 5px; background: none; border: none; cursor: pointer; font-size: 14px; display: ${isCollapsed ? 'none' : 'inline-block'}; transition: opacity 0.2s;">🔄</button>
        <button class="md-toggle-btn">${isCollapsed ? '◀' : '▼'}</button>
    `;

    const body = document.createElement('div');
    body.id = 'moodle-deadlines-body';

    if (!ungradedList || ungradedList.length === 0) {
        const emptyItem = document.createElement('div');
        emptyItem.className = 'md-item';
        emptyItem.innerHTML = `<div class="md-item-name" style="text-align: center; color: #28a745; padding: 10px 0;">🎉 Все работы проверены!</div>`;
        body.appendChild(emptyItem);
    } else {
        ungradedList.forEach(item => {
            const el = document.createElement('div');
            el.className = 'md-item';
            el.innerHTML = `
                <div class="md-item-header">
                    <div class="md-item-name">${item.title}</div>
                    <div class="md-item-time danger" style="font-weight: bold;">+${item.count} шт.</div>
                </div>
                <a href="${item.url}" target="_blank" class="md-item-btn" style="background-color: #198754; color: white;">Оценить работы</a>
            `;
            body.appendChild(el);
        });
    }

    widget.appendChild(header);
    widget.appendChild(body);
    document.body.appendChild(widget);

    header.addEventListener('click', (e) => {
        if (e.target.closest('.md-refresh-btn')) return;

        widget.classList.toggle('is-collapsed');
        const nowCollapsed = widget.classList.contains('is-collapsed');
        sessionStorage.setItem(storageKey, nowCollapsed ? 'collapsed' : 'expanded');

        const toggleBtn = header.querySelector('.md-toggle-btn');
        toggleBtn.innerHTML = nowCollapsed ? '◀' : '▼';

        const refreshBtn = header.querySelector('.md-refresh-btn');
        if (refreshBtn) refreshBtn.style.display = nowCollapsed ? 'none' : 'inline-block';
    });
}

function renderDeadlinesWidget(deadlines) {
    const processed = (deadlines || []).map(d => {
        const dateObj = parseRuDate(d.due_date);
        return { ...d, dateObj, daysLeft: getDaysLeft(dateObj) };
    }).filter(d => d.daysLeft >= 0).sort((a, b) => a.daysLeft - b.daysLeft);

    let widget = document.getElementById('moodle-deadlines-widget');
    if (widget) widget.remove();

    widget = document.createElement('div');
    widget.id = 'moodle-deadlines-widget';

    const storageKey = `moodle_deadlines_state_${getCourseId()}`;
    const isCollapsed = sessionStorage.getItem(storageKey) === 'collapsed';
    if (isCollapsed) {
        widget.classList.add('is-collapsed');
    }

    const header = document.createElement('div');
    header.id = 'moodle-deadlines-header';
    header.title = "Нажмите, чтобы развернуть/свернуть";

    const badgeHtml = processed.length > 0
        ? `<span style="position: absolute; top: -6px; right: -8px; background: #dc3545; color: white; font-size: 10px; font-weight: bold; padding: 2px 5px; border-radius: 10px; line-height: 1; border: 2px solid #343a40; z-index: 2;" title="Ожидают сдачи">${processed.length}</span>`
        : `<span style="position: absolute; top: -4px; right: -6px; background: #28a745; color: white; font-size: 9px; font-weight: bold; padding: 2px 4px; border-radius: 10px; line-height: 1; border: 2px solid #343a40; z-index: 2;" title="Всё сдано">✓</span>`;

    header.innerHTML = `
        <div style="position: relative; display: inline-flex; align-items: center; justify-content: center; margin-right: 12px; margin-left: 5px;">
            <span class="md-icon" style="margin-right: 0; font-size: 16px;">📅</span>
            ${badgeHtml}
        </div>
        <span class="md-title" style="flex-grow: 1;">Задания и Дедлайны</span>
        <button class="md-refresh-btn" title="Принудительно обновить" style="margin-left: 10px; margin-right: 5px; background: none; border: none; cursor: pointer; font-size: 14px; display: ${isCollapsed ? 'none' : 'inline-block'}; transition: opacity 0.2s;">🔄</button>
        <button class="md-toggle-btn">${isCollapsed ? '◀' : '▼'}</button>
    `;

    const body = document.createElement('div');
    body.id = 'moodle-deadlines-body';

    if (processed.length === 0) {
        const emptyItem = document.createElement('div');
        emptyItem.className = 'md-item';
        emptyItem.innerHTML = `<div class="md-item-name" style="text-align: center; color: #28a745; padding: 10px 0;">🎉 Нет горящих дедлайнов!</div>`;
        body.appendChild(emptyItem);
    } else {
        processed.forEach(d => {
            let timeClass = 'safe';
            if (d.daysLeft <= 1) timeClass = 'danger';
            else if (d.daysLeft <= 3) timeClass = 'warning';

            const timeLabel = buildDeadlineLabel(d.daysLeft);

            const item = document.createElement('div');
            item.className = 'md-item';
            item.innerHTML = `
                <div class="md-item-header">
                    <div class="md-item-name">${d.title}</div>
                    <div class="md-item-time ${timeClass}">${timeLabel}</div>
                </div>
                <div class="md-item-date">Срок сдачи: ${d.due_date}</div>
                <a href="${d.url}" target="_blank" class="md-item-btn">Сдать работу</a>
            `;
            body.appendChild(item);
        });
    }

    widget.appendChild(header);
    widget.appendChild(body);
    document.body.appendChild(widget);

    header.addEventListener('click', (e) => {
        if (e.target.closest('.md-refresh-btn')) return;

        widget.classList.toggle('is-collapsed');
        const nowCollapsed = widget.classList.contains('is-collapsed');
        sessionStorage.setItem(storageKey, nowCollapsed ? 'collapsed' : 'expanded');

        const toggleBtn = header.querySelector('.md-toggle-btn');
        toggleBtn.innerHTML = nowCollapsed ? '◀' : '▼';

        const refreshBtn = header.querySelector('.md-refresh-btn');
        if (refreshBtn) refreshBtn.style.display = nowCollapsed ? 'none' : 'inline-block';
    });
}

function toggleDeadlinesVisibility(isChatOpen) {
    const widget = document.getElementById('moodle-deadlines-widget');
    if (!widget) return;

    if (isChatOpen) {
        widget.style.opacity = '0';
        widget.style.pointerEvents = 'none';
        widget.style.transform = 'translateY(10px)';
    } else {
        widget.style.opacity = '1';
        widget.style.pointerEvents = 'auto';
        widget.style.transform = 'translateY(0)';
    }
}

// === НОВЫЕ ФУНКЦИИ ДЛЯ КОНТЕКСТА ===

function trimLongText(text, maxLen = 120000) {
    const value = (text || '').trim();
    if (value.length <= maxLen) return value;
    return value.slice(0, maxLen);
}

function getPageContextHtml() {
    try {
        const main = document.querySelector('[role="main"]') ||
                     document.querySelector('#region-main') ||
                     document.querySelector('main') ||
                     document.body;

        if (!main) return '';

        const clone = main.cloneNode(true);

        clone.querySelectorAll('script, style, noscript').forEach(el => el.remove());

        return trimLongText(clone.outerHTML, 180000);
    } catch (e) {
        return '';
    }
}

function getCourseTitleText() {
    try {
        const candidates = [
            document.querySelector('h1'),
            document.querySelector('.page-header-headings h1'),
            document.querySelector('#page-header h1'),
            document.querySelector('.page-context-header h1')
        ].filter(Boolean);

        if (candidates.length > 0) {
            const title = (candidates[0].innerText || '').trim();
            if (title) return title;
        }

        return document.title || '';
    } catch (e) {
        return '';
    }
}

function getTeachersText() {
    try {
        const blocks = [];

        const pageText = document.body ? document.body.innerText : '';
        const teacherMatches = pageText.match(/Преподавател[ья][\s\S]{0,800}/i);
        if (teacherMatches && teacherMatches[0]) {
            blocks.push(teacherMatches[0].slice(0, 800));
        }

        const selectors = [
            '.teachers',
            '.teachers-list',
            '.coursecontacts',
            '.course-contacts',
            '.teacher-info',
            '.course-info-container'
        ];

        selectors.forEach(sel => {
            document.querySelectorAll(sel).forEach(el => {
                const txt = (el.innerText || '').trim();
                if (txt) blocks.push(txt);
            });
        });

        const unique = [...new Set(blocks.map(x => x.trim()).filter(Boolean))];
        return trimLongText(unique.join('\n\n'), 4000);
    } catch (e) {
        return '';
    }
}

// function getCourseMapText() {
//     try {
//         const items = [];
//         document.querySelectorAll('li.activity').forEach((act) => {
//             if (typeof isActivityHidden === 'function' && isActivityHidden(act)) return;
//
//             const title = typeof extractModuleTitle === 'function'
//                 ? extractModuleTitle(act)
//                 : ((act.querySelector('.instancename, .activityname')?.innerText || '').trim());
//
//             if (!title) return;
//
//             let kind = 'other';
//             if (act.classList.contains('modtype_assign')) kind = 'lab';
//             else if (act.classList.contains('modtype_quiz')) kind = 'quiz';
//             else if (act.classList.contains('modtype_forum')) kind = 'forum';
//             else if (
//                 act.classList.contains('modtype_page') ||
//                 act.classList.contains('modtype_book') ||
//                 act.classList.contains('modtype_file') ||
//                 act.classList.contains('modtype_folder') ||
//                 act.classList.contains('modtype_url') ||
//                 act.classList.contains('modtype_lesson')
//             ) kind = 'lecture';
//
//             items.push(`${title} | type=${kind} | id=${act.id || ''}`);
//         });
//
//         return trimLongText(items.join('\n'), 12000);
//     } catch (e) {
//         return '';
//     }
// }

function getGradesText() {
    try {
        const bodyText = document.body ? document.body.innerText : '';
        const gradeHints = [];

        const patterns = [
            /Итог[\s\S]{0,400}/i,
            /Оценк[\s\S]{0,400}/i,
            /Баллы[\s\S]{0,400}/i
        ];

        patterns.forEach((pattern) => {
            const m = bodyText.match(pattern);
            if (m && m[0]) gradeHints.push(m[0]);
        });

        const unique = [...new Set(gradeHints.map(x => x.trim()).filter(Boolean))];
        return trimLongText(unique.join('\n\n'), 2500);
    } catch (e) {
        return '';
    }
}

function getAssignStatusText() {
    try {
        const selectors = [
            '.submissionstatustable',
            '.assignsubmission',
            '.submissionstatus',
            '.submissionsummarytable',
            '.activity-information'
        ];

        const parts = [];
        selectors.forEach(sel => {
            document.querySelectorAll(sel).forEach(el => {
                const txt = (el.innerText || '').trim();
                if (txt) parts.push(txt);
            });
        });

        const unique = [...new Set(parts.map(x => x.trim()).filter(Boolean))];
        return trimLongText(unique.join('\n\n'), 4000);
    } catch (e) {
        return '';
    }
}

// === ФУНКЦИИ ИНТЕРФЕЙСА ЧАТА ===

function highlightElement(targetId) {
    const el = document.getElementById(targetId);
    if (el) {
        el.scrollIntoView({ behavior: 'smooth', block: 'center' });
        el.classList.add('bot-highlight-animation');
        setTimeout(() => el.classList.remove('bot-highlight-animation'), 4000);
    }
}

function applyTextHighlight(textToHighlight) {
    if (!textToHighlight) return;

    if (!document.getElementById('bot-highlight-styles')) {
        const style = document.createElement('style');
        style.id = 'bot-highlight-styles';
        style.innerHTML = `
            @keyframes botPulse {
                0% { background-color: rgba(255, 193, 7, 0.1); border-left: 4px solid transparent; }
                20% { background-color: rgba(255, 193, 7, 0.3); border-left: 4px solid #007bff; transform: translateX(3px); }
                80% { background-color: rgba(255, 193, 7, 0.3); border-left: 4px solid #007bff; transform: translateX(0); }
                100% { background-color: transparent; border-left: 4px solid transparent; }
            }
            .bot-highlight-animation {
                animation: botPulse 1.5s infinite;
                border-radius: 4px;
            }
        `;
        document.head.appendChild(style);
    }

    setTimeout(() => {
        const mainContent = document.querySelector('[role="main"]') || document.querySelector('#region-main') || document.body;

        const sel = window.getSelection();
        sel.removeAllRanges();
        const range = document.createRange();
        range.selectNodeContents(mainContent);
        range.collapse(true);
        sel.addRange(range);

        const safeSnippet = textToHighlight.replace(/\n/g, ' ').substring(0, 30).trim();

        if (window.find(safeSnippet)) {
            const foundRange = sel.getRangeAt(0);
            let node = foundRange.commonAncestorContainer;

            if (node.nodeType === 3) node = node.parentNode;
            while (node && ['B', 'STRONG', 'SPAN', 'I', 'A'].includes(node.tagName)) node = node.parentNode;

            if (node && !['BODY', 'MAIN', 'HTML', 'SECTION'].includes(node.tagName)) {
                node.scrollIntoView({ behavior: 'smooth', block: 'center' });
                node.classList.add('bot-highlight-animation');
                setTimeout(() => node.classList.remove('bot-highlight-animation'), 4000);
            }
            sel.removeAllRanges();
        }
    }, 500);
}

function getHistoryForBackend() {
    const history = [];
    document.querySelectorAll('#moodle-bot-chat-messages .user-msg, #moodle-bot-chat-messages .bot-msg')
        .forEach(el => history.push({ role: el.classList.contains('user-msg') ? 'user' : 'assistant', content: (el.innerText || '').trim() }));
    return history.slice(-6);
}

function injectChatUI() {
    if (document.getElementById('moodle-bot-btn')) return;

    if (!document.getElementById('moodle-bot-badge-fix')) {
        const style = document.createElement('style');
        style.id = 'moodle-bot-badge-fix';
        style.innerHTML = `
            #moodle-bot-btn {
                overflow: visible !important; 
            }
            #moodle-bot-btn img {
                border-radius: 50% !important;
                display: block;
            }
            #moodle-bot-badge {
                position: absolute !important;
                top: -6px !important;
                right: -6px !important;
                background-color: #dc3545 !important;
                color: white !important;
                font-size: 11px !important;
                font-weight: bold !important;
                padding: 2px 6px !important;
                border-radius: 12px !important;
                border: 2px solid white !important;
                z-index: 9999 !important;
                box-shadow: 0 2px 5px rgba(0,0,0,0.3) !important;
                transition: transform 0.2s !important;
            }
        `;
        document.head.appendChild(style);
    }

    const btn = document.createElement('div');
    btn.id = 'moodle-bot-btn';

    btn.innerHTML = `
        <img src="${chrome.runtime.getURL('assets/logo.png')}" alt="Moodle Bot">
        <span id="moodle-bot-badge" style="display: none !important;">0</span>
    `;
    document.body.appendChild(btn);

    const chatWindow = document.createElement('div');
    chatWindow.id = 'moodle-bot-chat';

    chatWindow.innerHTML = `
        <div id="moodle-bot-chat-header">
            <span>Moodle Assistant</span>
            <div class="moodle-bot-header-actions">
                <button id="moodle-bot-resize-btn" title="Развернуть">⛶</button>
                <button id="moodle-bot-close-btn" title="Закрыть">✖</button>
            </div>
        </div>
        <div id="moodle-bot-chat-messages"></div>
        <div id="moodle-bot-chat-input-area">
            <input type="text" id="moodle-bot-chat-input" placeholder="Введите ваш вопрос..." autocomplete="off">
            <button id="moodle-bot-chat-send">▶</button>
        </div>
    `;
    document.body.appendChild(chatWindow);

    const messagesArea = document.getElementById('moodle-bot-chat-messages');
    const sendBtn = document.getElementById('moodle-bot-chat-send');
    const inputField = document.getElementById('moodle-bot-chat-input');
    const historyKey = `moodle_bot_chat_history_${getCourseId()}`;

    const resizeBtn = document.getElementById('moodle-bot-resize-btn');
    const closeBtn = document.getElementById('moodle-bot-close-btn');

    let isExpanded = false;
    let isChatOpen = false;
    let unreadCount = 0;

    resizeBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        isExpanded = !isExpanded;
        chatWindow.classList.toggle('expanded', isExpanded);
        resizeBtn.innerHTML = isExpanded ? '🗗' : '⛶';
        resizeBtn.title = isExpanded ? 'Уменьшить' : 'Развернуть';
        setTimeout(() => messagesArea.scrollTop = messagesArea.scrollHeight, 310);
    });

    closeBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        spinCircle();
        isChatOpen = false;
        chatWindow.classList.remove('is-open');
        btn.classList.remove('is-active');
        toggleDeadlinesVisibility(false);
    });

    btn.addEventListener('click', () => {
        spinCircle();
        isChatOpen = !isChatOpen;

        if (isChatOpen) {
            unreadCount = 0;
            const badge = document.getElementById('moodle-bot-badge');
            if (badge) {
                badge.style.setProperty('display', 'none', 'important');
                badge.innerText = '0';
            }
            setTimeout(() => {
                messagesArea.scrollTop = messagesArea.scrollHeight;
                inputField.focus();
            }, 180);
        }

        chatWindow.classList.toggle('is-open', isChatOpen);
        btn.classList.toggle('is-active', isChatOpen);

        if (typeof toggleDeadlinesVisibility === 'function') {
            toggleDeadlinesVisibility(isChatOpen);
        } else {
            console.warn('Функция toggleDeadlinesVisibility не найдена!');
        }
    });

    messagesArea.innerHTML = sessionStorage.getItem(historyKey) || `<div class="bot-msg">Привет! Я помощник по этому курсу. Напишите тему, и я подскажу, где это находится.</div>`;

    const addMessageToChat = function(htmlString) {
        messagesArea.innerHTML += htmlString;
        sessionStorage.setItem(historyKey, messagesArea.innerHTML);
        messagesArea.scrollTop = messagesArea.scrollHeight;

        if (!chatWindow.classList.contains('is-open')) {
            unreadCount++;
            const badge = document.getElementById('moodle-bot-badge');
            if (badge) {
                badge.innerText = unreadCount;
                badge.style.setProperty('display', 'block', 'important');
                badge.style.setProperty('transform', 'scale(1.2)', 'important');
                setTimeout(() => badge.style.setProperty('transform', 'scale(1)', 'important'), 200);
            }
        }
    };
    window.MoodleBot.addMessageToChat = addMessageToChat;

    messagesArea.addEventListener('click', (e) => {
        const targetBtn = e.target.closest('.moodle-bot-target-btn');
        if (!targetBtn) return;

        const targetUrl = targetBtn.getAttribute('data-url');
        const targetId = targetBtn.getAttribute('data-id');
        const targetSnippet = targetBtn.getAttribute('data-snippet');

        if (targetSnippet) sessionStorage.setItem('moodle_bot_highlight_text', targetSnippet);

        const currentUrlObj = new URL(window.location.href);
        const targetUrlObj = new URL(targetUrl);

        if (currentUrlObj.pathname === targetUrlObj.pathname && currentUrlObj.searchParams.get('id') === targetUrlObj.searchParams.get('id')) {
            if (targetSnippet) applyTextHighlight(targetSnippet);
            highlightElement(targetId);
        } else {
            sessionStorage.setItem('moodle_bot_teleport_target', targetId);
            window.location.href = targetUrl;
        }
    });

    const teleportMsg = sessionStorage.getItem('moodle_bot_teleport_msg');
    if (teleportMsg) {
        chatWindow.classList.add('is-open');
        btn.classList.add('is-active');
        isChatOpen = true;
        toggleDeadlinesVisibility(true);
        addMessageToChat(`<div class="bot-msg">${teleportMsg}</div>`);
        sessionStorage.removeItem('moodle_bot_teleport_msg');
    }

    let isSending = false;

    const sendMessage = async () => {
        if (isSending) return;
        const text = inputField.value.trim();
        if (!text) return;

        isSending = true;
        inputField.disabled = true;
        sendBtn.style.opacity = "0.5";
        addMessageToChat(`<div class="user-msg">${text}</div>`);
        inputField.value = '';

        try {
            const payload = {
                course_id: getCourseId(),
                message: text,
                history: getHistoryForBackend(),
                viewer_role: getViewerRole(),
                deadlines: window.MoodleBot.activeDeadlines || [],
                course_title: getCourseTitleText(),
                course_map: getCourseMap(),
                teachers: getTeachersText(),
                page_context: getPageContextHtml(),
                grades: getGradesText(),
                assign_status: getAssignStatusText()
            };

            const response = await fetch(`http://127.0.0.1:8000/api/smart-search`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload)
            });

            if (!response.ok) throw new Error("Ошибка сервера");
            const data = await response.json();

            let finalHtml = `<div class="bot-msg">${(data.reply || '')
                .replace(/\n/g, '<br>')
                .replace(/\*\*(.*?)\*\*/g, '<b>$1</b>')
                .replace(/\* /g, '<br>• ')}</div>`;

            if (data.targets && data.targets.length > 0) {
                finalHtml += `<div style="display: flex; flex-direction: column; gap: 4px;">`;
                data.targets.forEach(t => {
                    finalHtml += `<button class="moodle-bot-target-btn" data-url="${t.url}" data-id="${t.id}" data-snippet="${t.snippet || ''}">🎯 Переход: ${t.title}</button>`;
                });
                finalHtml += `</div>`;
            }

            if (data.debug_meta || data.debug_context) {
                finalHtml += `<details style="margin-top:8px;"><summary style="cursor:pointer; color:#6c757d;">🐛 Дебаг-информация</summary>`;

                if (data.debug_meta) {
                    finalHtml += `<div style="font-size:12px; margin-top:6px;">`;
                    finalHtml += `<b>goal:</b> ${data.debug_meta.goal || ''}<br>`;
                    finalHtml += `<b>target:</b> ${data.debug_meta.target || ''}<br>`;
                    finalHtml += `<b>chosen_title:</b> ${data.debug_meta.chosen_title || ''}<br>`;
                    finalHtml += `<b>chosen_kind:</b> ${data.debug_meta.chosen_kind || ''}<br>`;
                    finalHtml += `</div>`;
                }

                if (Array.isArray(data.debug_context) && data.debug_context.length > 0) {
                    finalHtml += `<div style="font-size:12px; margin-top:8px; display:flex; flex-direction:column; gap:6px;">`;
                    data.debug_context.forEach(item => {
                        finalHtml += `
                            <div style="padding:6px 8px; background:#f8f9fa; border-radius:6px; border-left:3px solid #dee2e6;">
                                <div><b>${item.title || ''}</b> ${typeof item.score !== 'undefined' ? `(Score: ${item.score})` : ''}</div>
                                <div style="margin-top:4px; color:#495057;">${(item.text || '').replace(/\n/g, '<br>')}</div>
                            </div>
                        `;
                    });
                    finalHtml += `</div>`;
                }

                finalHtml += `</details>`;
            }

            addMessageToChat(finalHtml);

        } catch (error) {
            addMessageToChat(`<div class="bot-msg" style="color:red;">Связь с сервером потеряна. Проверьте соединение.</div>`);
        } finally {
            isSending = false;
            inputField.disabled = false;
            sendBtn.style.opacity = "1";
            inputField.focus();
        }
    };

    sendBtn.addEventListener('click', sendMessage);
    inputField.addEventListener('keypress', (e) => { if (e.key === 'Enter') sendMessage(); });
}

async function getUngradedAssignments() {
    const doms = await getCourseDOMs();
    const assignments = [];
    const seenAssigns = new Set();

    doms.forEach(doc => {
        doc.querySelectorAll('li.activity.modtype_assign').forEach(act => {
            if (seenAssigns.has(act.id)) return;
            seenAssigns.add(act.id);

            const a = act.querySelector('a.aalink');
            if (a) assignments.push({ id: act.id, title: extractModuleTitle(act), url: a.href });
        });
    });

    const ungradedList = [];

    for (let i = 0; i < assignments.length; i += 3) {
        const chunk = assignments.slice(i, i + 3);
        const promises = chunk.map(async (assign) => {
            try {
                const response = await fetch(assign.url, { credentials: 'include' });
                const html = await response.text();
                const doc = new DOMParser().parseFromString(html, "text/html");

                let count = 0;
                const rows = doc.querySelectorAll('.assignsummary tr, table.generaltable tr');

                for (const row of rows) {
                    const cells = row.querySelectorAll('th, td');

                    if (cells.length >= 2) {
                        const headerText = (cells[0].textContent || "").toLowerCase().trim();

                        if (headerText.includes('требуют оценки') || headerText.includes('needs grading') || headerText.includes('ожидают оценки')) {
                            const valueCell = cells[1].cloneNode(true);
                            valueCell.querySelectorAll('.accesshide, .hidden, .sr-only').forEach(el => el.remove());

                            const match = valueCell.textContent.match(/(\d+)/);
                            if (match) {
                                count = parseInt(match[1], 10);
                                break;
                            }
                        }
                    }
                }

                if (count > 0) {
                    ungradedList.push({
                        title: assign.title,
                        url: assign.url + '&action=grading',
                        count: count
                    });
                }
            } catch (e) {}
        });
        await Promise.all(promises);
    }

    return ungradedList.sort((a, b) => b.count - a.count);
}

// === ТОЧКА ВХОДА ===
setTimeout(async () => {
    injectChatUI();

    const targetId = sessionStorage.getItem('moodle_bot_teleport_target');
    if (targetId) {
        setTimeout(() => highlightElement(targetId), 1000);
        sessionStorage.removeItem('moodle_bot_teleport_target');
    }

    const textToHighlight = sessionStorage.getItem('moodle_bot_highlight_text');
    if (textToHighlight) {
        sessionStorage.removeItem('moodle_bot_highlight_text');
        applyTextHighlight(textToHighlight);
    }

    const path = window.location.pathname;

    if (path.includes('/course/view.php')) {
        parseCourseIndex();
        const courseId = getCourseId();

        if (getViewerRole() === 'student') {
            const deadlinesCacheKey = `moodle_bot_deadlines_cache_${courseId}`;
            const cacheTimeKey = `moodle_bot_deadlines_time_${courseId}`;

            const processStudentData = async (deadlines) => {
                window.MoodleBot.activeDeadlines = deadlines;
                renderDeadlinesWidget(deadlines);

                const processedDeadlines = deadlines.map(d => ({ ...d, daysLeft: getDaysLeft(parseRuDate(d.due_date)) })).filter(d => d.daysLeft >= 0).sort((a, b) => a.daysLeft - b.daysLeft);

                const onboardingKey = `moodle_bot_onboarded_${courseId}`;
                if (!localStorage.getItem(onboardingKey)) {
                    let quizCount = 0; let assignCount = 0;
                    const doms = await getCourseDOMs();
                    doms.forEach(doc => {
                        quizCount += Array.from(doc.querySelectorAll('li.activity.modtype_quiz')).filter(el => !isActivityHidden(el)).length;
                        assignCount += Array.from(doc.querySelectorAll('li.activity.modtype_assign')).filter(el => !isActivityHidden(el)).length;
                    });

                    let welcomeMsg = `Привет! 👋 Я твой ИИ-помощник по этому курсу. Давай посмотрим, что нас ждет впереди:<br><br>📝 <b>Практических заданий:</b> ${assignCount}<br>🧠 <b>Тестов:</b> ${quizCount}<br><br>`;

                    if (processedDeadlines.length > 0) {
                        const nearest = processedDeadlines[0];
                        welcomeMsg += `🚨 <b>Ближайший дедлайн:</b> ${nearest.title} (до ${nearest.due_date}).<br><button class="moodle-bot-target-btn" data-url="${nearest.url}" style="margin-top: 5px; width: 100%; background: #e3f2fd; border: 1px solid #90caf9; border-radius: 6px; cursor: pointer; color: #004085; font-size: 13px; padding: 6px;">Перейти к заданию</button><br>`;
                    } else {
                        welcomeMsg += `🎉 <b>Горящих дедлайнов пока нет.</b> Можно спокойно изучать теорию.<br><br>`;
                    }
                    welcomeMsg += `Если нужно найти лекцию — просто напиши мне!`;

                    setTimeout(() => {
                        if (window.MoodleBot && window.MoodleBot.addMessageToChat) {
                            window.MoodleBot.addMessageToChat(`<div class="bot-msg">${welcomeMsg}</div>`);
                        }
                    }, 1000);
                    localStorage.setItem(onboardingKey, 'true');
                }

                const urgentDeadlines = processedDeadlines.filter(d => d.daysLeft <= 1);

                if (urgentDeadlines.length > 0) {
                    const warnedKey = `moodle_bot_warned_deadlines_${courseId}`;
                    let warnedIds = [];
                    try {
                        warnedIds = JSON.parse(localStorage.getItem(warnedKey)) || [];
                    } catch (e) {}

                    const newUrgentDeadlines = urgentDeadlines.filter(d => !warnedIds.includes(d.moodle_id));

                    if (newUrgentDeadlines.length > 0) {
                        let warnMsg = `🚨 <b>Внимание! Горит дедлайн!</b><br>У вас есть задания, срок сдачи которых истекает менее чем через сутки:<br><br>`;

                        newUrgentDeadlines.forEach(d => {
                            warnMsg += `• <b>${d.title}</b><br>`;
                            warnMsg += `<button class="moodle-bot-target-btn" data-url="${d.url}" style="margin-top: 5px; margin-bottom: 10px; width: 100%; background: #ffeeba; border: 1px solid #ffdf7e; border-radius: 6px; cursor: pointer; color: #856404; font-size: 13px; padding: 6px;">Срочно сдать работу</button>`;
                            warnedIds.push(d.moodle_id);
                        });

                        setTimeout(() => {
                            if (window.MoodleBot && window.MoodleBot.addMessageToChat) {
                                window.MoodleBot.addMessageToChat(`<div class="bot-msg" style="border-left: 4px solid #dc3545; background: #fff8e5;">${warnMsg}</div>`);
                            }
                        }, 2500);

                        localStorage.setItem(warnedKey, JSON.stringify(warnedIds));
                    }
                }
            };

            const fetchStudentData = async (forceRefresh = false) => {
                const now = new Date().getTime();
                const cachedDeadlines = sessionStorage.getItem(deadlinesCacheKey);
                const cachedTime = sessionStorage.getItem(cacheTimeKey);

                if (!forceRefresh && cachedDeadlines && cachedTime && (now - parseInt(cachedTime) < 1800000)) {
                    processStudentData(JSON.parse(cachedDeadlines));
                } else {
                    const btn = document.querySelector('.md-refresh-btn');
                    if (btn) btn.style.opacity = '0.5';

                    const deadlines = await extractDeadlinesFromCourse();
                    sessionStorage.setItem(deadlinesCacheKey, JSON.stringify(deadlines));
                    sessionStorage.setItem(cacheTimeKey, now.toString());
                    processStudentData(deadlines);
                }
            };

            fetchStudentData();

            document.addEventListener('click', (e) => {
                const btn = e.target.closest('.md-refresh-btn');
                if (btn) {
                    e.stopPropagation();
                    fetchStudentData(true);
                }
            });

            setTimeout(async () => {
                const storageKey = `moodle_bot_known_modules_${courseId}`;
                const knownModulesStr = localStorage.getItem(storageKey);
                const currentModules = [];
                const doms = await getCourseDOMs();

                doms.forEach(doc => {
                    doc.querySelectorAll('li.activity').forEach(act => {
                        const a = act.querySelector('a.aalink');
                        if (!isActivityHidden(act) && a) {
                            const title = extractModuleTitle(act);
                            if (title) currentModules.push({ id: act.id, title: title, url: a.href });
                        }
                    });
                });

                if (knownModulesStr) {
                    const knownModuleIds = new Set(JSON.parse(knownModulesStr));
                    const newModules = currentModules.filter(m => !knownModuleIds.has(m.id));

                    if (newModules.length > 0) {
                        let msg = `🔔 <b>На курсе появились новые материалы!</b><br><br>`;
                        let buttonsHtml = '<div style="display: flex; flex-direction: column; gap: 4px;">';
                        newModules.slice(0, 3).forEach(m => { buttonsHtml += `<button class="moodle-bot-target-btn" data-url="${m.url}" data-id="${m.id}" style="text-align: left; padding: 6px 10px; background: #e3f2fd; border: 1px solid #90caf9; border-radius: 6px; cursor: pointer; color: #004085; font-size: 12px;">🆕 Открыть: ${m.title}</button>`; });
                        msg += buttonsHtml + '</div>';
                        if (newModules.length > 3) msg += `<div style="font-size: 11px; color: #6c757d; margin-top: 6px;">И еще ${newModules.length - 3} других...</div>`;

                        if (window.MoodleBot && window.MoodleBot.addMessageToChat) {
                            window.MoodleBot.addMessageToChat(`<div class="bot-msg" style="border-left: 4px solid #0d6efd;">${msg}</div>`);
                        }
                    }
                }
                localStorage.setItem(storageKey, JSON.stringify(currentModules.map(m => m.id)));
            }, 3000);

        } else if (getViewerRole() === 'teacher') {
            const ungradedCacheKey = `moodle_bot_ungraded_cache_${courseId}`;
            const cacheTimeKey = `moodle_bot_ungraded_time_${courseId}`;

            const teacherKey = `moodle_bot_teacher_notified_${courseId}`;
            const today = new Date().toLocaleDateString('ru-RU');

            const processTeacherData = (ungradedList) => {
                renderUngradedWidget(ungradedList);

                if (localStorage.getItem(teacherKey) !== today) {
                    if (ungradedList.length > 0) {
                        const totalCount = ungradedList.reduce((sum, item) => sum + item.count, 0);
                        let msg = `🎓 <b>Приветствую, коллега!</b><br>У вас накопились непроверенные работы: <b>${totalCount} шт.</b><br><br>`;
                        let buttonsHtml = '<div style="display: flex; flex-direction: column; gap: 4px;">';

                        ungradedList.forEach(item => { buttonsHtml += `<button class="moodle-bot-target-btn" data-url="${item.url}" style="text-align: left; padding: 6px 10px; background: #f8f9fa; border: 1px solid #ced4da; border-left: 4px solid #198754; border-radius: 6px; cursor: pointer; color: #212529; font-size: 12px;">📄 ${item.title} <span style="float: right; font-weight: bold; color: #198754;">+${item.count}</span></button>`; });
                        msg += buttonsHtml + '</div>';

                        if (window.MoodleBot && window.MoodleBot.addMessageToChat) {
                            window.MoodleBot.addMessageToChat(`<div class="bot-msg" style="border-left: 4px solid #198754; background: #f8fff9;">${msg}</div>`);
                        }
                    }
                    localStorage.setItem(teacherKey, today);
                }
            };

            const fetchTeacherData = async (forceRefresh = false) => {
                const now = new Date().getTime();
                const cachedUngraded = sessionStorage.getItem(ungradedCacheKey);
                const cachedTime = sessionStorage.getItem(cacheTimeKey);

                if (!forceRefresh && cachedUngraded && cachedTime && (now - parseInt(cachedTime) < 1800000)) {
                    processTeacherData(JSON.parse(cachedUngraded));
                } else {
                    const btn = document.querySelector('.md-refresh-btn');
                    if (btn) btn.style.opacity = '0.5';

                    const ungradedList = await getUngradedAssignments();
                    sessionStorage.setItem(ungradedCacheKey, JSON.stringify(ungradedList));
                    sessionStorage.setItem(cacheTimeKey, now.toString());
                    processTeacherData(ungradedList);
                }
            };

            fetchTeacherData();

            document.addEventListener('click', (e) => {
                const btn = e.target.closest('.md-refresh-btn');
                if (btn) {
                    e.stopPropagation();
                    fetchTeacherData(true);
                }
            });

        }
    } else if (path.includes('/mod/')) {
        passiveModuleSync();
    }
}, 1500);