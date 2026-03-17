// === 1. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ И ДЕДЛАЙНЫ ===

window.courseActiveDeadlines = [];
let courseDOMsCache = null;

async function getCourseDOMs() {
    if (courseDOMsCache) return courseDOMsCache;

    const doms = [document];
    const sectionLinks = Array.from(document.querySelectorAll('h3.sectionname a[href*="section="]')).map(a => a.href);

    if (sectionLinks.length > 0) {
        console.log(`[Moodle Bot] Найдена пагинация. Загружаю ${sectionLinks.length} скрытых разделов...`);
        for (let i = 0; i < sectionLinks.length; i += 3) {
            const chunk = sectionLinks.slice(i, i + 3);
            const promises = chunk.map(async (url) => {
                try {
                    const res = await fetch(url, { credentials: 'include' });
                    const html = await res.text();
                    return new DOMParser().parseFromString(html, "text/html");
                } catch (e) { return null; }
            });
            const results = await Promise.all(promises);
            doms.push(...results.filter(Boolean));
            await new Promise(r => setTimeout(r, 200));
        }
    }

    courseDOMsCache = doms;
    return doms;
}

function getCourseId() {
    const match = document.body.className.match(/course-(\d+)/);
    if (match) return match[1];
    const urlParams = new URLSearchParams(window.location.search);
    return urlParams.get('id') || "unknown";
}

function isTeacherView() {
    return document.querySelector('form[action*="editmode.php"]') !== null;
}

function getViewerRole() {
    return isTeacherView() ? 'teacher' : 'student';
}

function hasRestrictionMarkers(actElement) {
    if (!actElement) return false;
    if (actElement.querySelector('.availabilityinfo')) return true;
    if (actElement.querySelector('.badge-warning')) return true;
    if (actElement.querySelector('.conditionalhidden')) return true;
    const txt = (actElement.innerText || '').toLowerCase();
    return txt.includes('доступно') || txt.includes('недоступно') || txt.includes('услов');
}

function extractVisibilityInfo(actElement) {
    const text = cleanText(actElement?.innerText || '');
    return {
        is_hidden: isActivityHidden(actElement),
        has_restrictions: hasRestrictionMarkers(actElement) && !isActivityHidden(actElement),
        raw_text: text.slice(0, 1200)
    };
}

function isActivityHidden(actElement) {
    if (!actElement) return false;
    if (actElement.querySelector('.hiddenactivity')) return true;
    if (actElement.querySelector('.conditionalhidden')) return true;

    const badges = actElement.querySelectorAll('.badge-warning');
    for (const b of badges) {
        if ((b.innerText || '').toLowerCase().includes('скрыто')) return true;
    }
    return false;
}

function parseRuDate(dateStr) {
    if (!dateStr) return null;

    const normalized = dateStr.trim().toLowerCase().replace(/\s+г\.?$/, '');
    const cleanDate = normalized.replace(/\d{1,2}:\d{2}/, '').trim();

    const patterns = [
        {
            regex: /^(\d{1,2})\.(\d{1,2})\.(\d{4})$/,
            handler: (m) => new Date(parseInt(m[3]), parseInt(m[2]) - 1, parseInt(m[1]), 23, 59, 0)
        },
        {
            regex: /^(\d{1,2})\s+([а-яё]+)\s+(\d{4})$/i,
            handler: (m) => {
                const monthMap = {
                    'января': 0, 'февраля': 1, 'марта': 2, 'апреля': 3, 'мая': 4, 'июня': 5,
                    'июля': 6, 'августа': 7, 'сентября': 8, 'октября': 9, 'ноября': 10, 'декабря': 11
                };
                const month = monthMap[m[2].toLowerCase()];
                return month !== undefined
                    ? new Date(parseInt(m[3]), month, parseInt(m[1]), 23, 59, 0)
                    : null;
            }
        }
    ];

    for (const p of patterns) {
        const match = cleanDate.match(p.regex);
        if (match) return p.handler(match);
    }
    return null;
}

function cleanText(text) {
    return (text || '').replace(/\s+/g, ' ').trim();
}

function extractModuleTitle(act) {
    if (!act) return "Без названия";

    const selectors = [
        '.instancename',
        '.aalink .instancename',
        '.activityname',
        '.name',
        'a.aalink'
    ];

    for (const selector of selectors) {
        const el = act.querySelector(selector);
        if (el) {
            let text = cleanText(el.innerText || el.textContent || '');
            text = text.replace(/\s*Файл\s*$/i, '');
            text = text.replace(/\s*URL\s*$/i, '');
            text = text.replace(/\s*Папка\s*$/i, '');
            text = text.replace(/\s*Страница\s*$/i, '');
            text = text.replace(/\s*Книга\s*$/i, '');
            text = text.replace(/\s*Отметить как выполненный\s*$/i, '');
            if (text) return text;
        }
    }

    return cleanText(act.getAttribute('data-activityname') || act.innerText || 'Без названия');
}

function getModuleType(act) {
    const match = act.className.match(/modtype_(\w+)/);
    return match ? match[1] : null;
}

function extractMeaningfulContent(doc) {
    const mainNode =
        doc.querySelector('[role="main"]') ||
        doc.querySelector('#region-main') ||
        doc.querySelector('.resourcecontent') ||
        doc.querySelector('.box.generalbox') ||
        doc.querySelector('.book_content') ||
        doc.body;

    if (!mainNode) return '';

    const clone = mainNode.cloneNode(true);

    const selectorsToRemove = [
        '#nav-drawer',
        '[data-region="drawer"]',
        '[data-region="message-drawer"]',
        '.popover-region',
        '#page-footer',
        'footer',
        'nav',
        '.navbar',
        '.block',
        '.activity-navigation',
        '.sr-only',
        '.hidden',
        'script',
        'style'
    ];

    selectorsToRemove.forEach(selector => {
        clone.querySelectorAll(selector).forEach(el => el.remove());
    });

    let text = clone.innerText || clone.textContent || '';
    return cleanText(text);
}

function shouldIndexModuleType(type) {
    return [
        'page',
        'resource',
        'assign',
        'book',
        'quiz',
        'url',
        'label',
        'lesson',
        'folder',
        'forum',
        'chat',
        'checklist'
    ].includes(type);
}

function isFileActivity(act) {
    if (!act) return false;

    const icon = act.querySelector('img.icon');
    if (icon && icon.src) {
        const src = icon.src.toLowerCase();
        const fileIcons = ['/f/pdf', '/f/document', '/f/spreadsheet', '/f/powerpoint', '/f/archive', '/f/text', '/f/word', '/f/excel'];
        if (fileIcons.some(f => src.includes(f))) return true;
    }

    const typeEl = act.querySelector('.accesshide');
    if (typeEl && typeEl.innerText.toLowerCase().includes('файл')) return true;

    return false;
}

async function extractDeadlinesFromCourse() {
    const doms = await getCourseDOMs();
    const deadlines = [];
    const seenAssigns = new Set();

    for (const doc of doms) {
        const assignments = doc.querySelectorAll('li.activity.modtype_assign');

        for (const act of assignments) {
            if (isActivityHidden(act) || seenAssigns.has(act.id)) continue;
            seenAssigns.add(act.id);

            const a = act.querySelector('a.aalink');
            if (!a) continue;

            try {
                const response = await fetch(a.href, { credentials: 'include' });
                const html = await response.text();
                const assignDoc = new DOMParser().parseFromString(html, "text/html");

                const statusEl = assignDoc.querySelector('.submissionstatus, .submissionstatussubmitted, .badge-success');
                if (statusEl && /выполнено|отправлено|submitted/i.test(statusEl.innerText)) continue;

                const dueTextMatch = assignDoc.body.innerText.match(/Срок сдачи:\s*(?:[а-яё]+,\s*)?(\d{1,2}\s+[а-яё]+ \d{4})/i);
                if (dueTextMatch && dueTextMatch[1]) {
                    const dueDate = parseRuDate(dueTextMatch[1]);
                    if (dueDate) {
                        deadlines.push({
                            title: a.innerText.trim(),
                            due_date_raw: dueTextMatch[1],
                            due_date: dueDate,
                            url: a.href,
                            moodle_id: act.id
                        });
                    }
                }
            } catch (e) {}
        }
    }

    window.courseActiveDeadlines = deadlines.map(d => ({
        title: d.title,
        due_date: d.due_date.toLocaleDateString('ru-RU'),
        url: d.url
    }));

    return deadlines;
}

function getDaysLeft(date) {
    const today = new Date(new Date().setHours(0, 0, 0, 0));
    const target = new Date(date.getFullYear(), date.getMonth(), date.getDate());
    return Math.ceil((target - today) / (1000 * 60 * 60 * 24));
}

function buildDeadlineLabel(date) {
    const daysLeft = getDaysLeft(date);
    if (daysLeft < 0) return 'Срок уже прошёл';
    if (daysLeft === 0) return 'Сегодня дедлайн';
    if (daysLeft === 1) return 'Завтра дедлайн';
    return daysLeft <= 3 ? `Через ${daysLeft} дн. дедлайн` : 'Ближайшая работа';
}

const minimizedDeadlines = [];

function showDeadlineNotification(deadline) {
    if (!deadline) return;

    const storageKey = `moodle_deadline_${getCourseId()}_${deadline.title}`;
    if (localStorage.getItem(storageKey) === new Date().toLocaleDateString('ru-RU')) return;

    const bubble = document.createElement('div');
    bubble.classList.add('moodle-deadline-bubble');
    bubble.innerHTML = `
        <div class="moodle-deadline-badge">${buildDeadlineLabel(deadline.due_date)}</div>
        <div class="moodle-deadline-text">Прикрепите работу <b>${deadline.title}</b> до <b>${deadline.due_date.toLocaleDateString('ru-RU')}</b></div>
        <div class="moodle-deadline-actions">
            <button class="moodle-deadline-open">Открыть</button>
            <button class="moodle-deadline-minimize">_</button>
            <button class="moodle-deadline-close">×</button>
        </div>
    `;
    document.getElementById('moodle-deadlines-container').appendChild(bubble);

    bubble.querySelector('.moodle-deadline-open')?.addEventListener('click', () => window.open(deadline.url, '_blank'));
    bubble.querySelector('.moodle-deadline-minimize')?.addEventListener('click', () => {
        bubble.style.display = 'none';
        minimizedDeadlines.push(deadline);
        updateMinimizedChatButton();
    });
    bubble.querySelector('.moodle-deadline-close')?.addEventListener('click', () => {
        localStorage.setItem(storageKey, new Date().toLocaleDateString('ru-RU'));
        bubble.remove();
    });
}

function updateMinimizedChatButton() {
    if (!document.getElementById('moodle-minimized-btn')) {
        const btn = document.createElement('button');
        btn.id = 'moodle-minimized-btn';
        btn.innerHTML = `<video src="https://cdn-icons-mp4.flaticon.com/512/11919/11919421.mp4" autoplay loop muted playsinline style="width:40px; border-radius:50%"></video>`;
        document.body.appendChild(btn);
        btn.addEventListener('click', () => {
            minimizedDeadlines.forEach(showDeadlineNotification);
            minimizedDeadlines.length = 0;
            btn.remove();
        });
    }
}

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
                20% { background-color: rgba(255, 193, 7, 0.3); border-left: 4px solid #ffc107; transform: translateX(3px); }
                80% { background-color: rgba(255, 193, 7, 0.3); border-left: 4px solid #ffc107; transform: translateX(0); }
                100% { background-color: transparent; border-left: 4px solid transparent; }
            }
            .bot-highlight-animation {
                animation: botPulse 4s ease-in-out !important;
                border-radius: 2px;
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
            while (node && (node.tagName === 'B' || node.tagName === 'STRONG' || node.tagName === 'SPAN' || node.tagName === 'I' || node.tagName === 'A')) {
                node = node.parentNode;
            }

            const forbiddenTags = ['BODY', 'MAIN', 'HTML', 'SECTION'];
            if (node && !forbiddenTags.includes(node.tagName)) {
                node.scrollIntoView({ behavior: 'smooth', block: 'center' });
                node.classList.add('bot-highlight-animation');
                setTimeout(() => node.classList.remove('bot-highlight-animation'), 4000);
            }
            sel.removeAllRanges();
        } else {
            console.log("[Moodle Bot] Фрагмент не найден на странице:", safeSnippet);
        }
    }, 500);
}

function getHistoryForBackend() {
    const history = [];
    document.querySelectorAll('#moodle-bot-chat-messages .user-msg, #moodle-bot-chat-messages .bot-msg')
        .forEach(el => {
            history.push({
                role: el.classList.contains('user-msg') ? 'user' : 'assistant',
                content: (el.innerText || '').trim()
            });
        });

    return history.slice(-6);
}


// === 2. ИНТЕРФЕЙС И ЛОГИКА ЧАТА ===
function injectChatUI() {
    if (!document.getElementById('moodle-deadlines-container')) {
        const c = document.createElement('div');
        c.id = 'moodle-deadlines-container';
        document.body.appendChild(c);
    }

    const btn = document.createElement('div');
    btn.id = 'moodle-bot-btn';
    btn.innerHTML = '🤖';
    document.body.appendChild(btn);

    const chatWindow = document.createElement('div');
    chatWindow.id = 'moodle-bot-chat';

    chatWindow.innerHTML = `
        <div id="moodle-bot-chat-header" style="display: flex; justify-content: space-between; align-items: center; padding: 10px 15px;">
            <span>Moodle Assistant</span>
            <button id="moodle-bot-resize-btn" title="Развернуть" style="background: none; border: none; color: white; cursor: pointer; font-size: 18px; padding: 0; display: flex; align-items: center; justify-content: center; height: 24px; width: 24px; transition: 0.2s;">⛶</button>
        </div>
        <div id="moodle-bot-chat-messages"></div>
        <div id="moodle-bot-chat-input-area">
            <input type="text" id="moodle-bot-chat-input" placeholder="Введите ваш вопрос...">
            <button id="moodle-bot-chat-send">▶</button>
        </div>
    `;
    document.body.appendChild(chatWindow);

    const messagesArea = document.getElementById('moodle-bot-chat-messages');
    const sendBtn = document.getElementById('moodle-bot-chat-send');
    const inputField = document.getElementById('moodle-bot-chat-input');
    const historyKey = `moodle_bot_chat_history_${getCourseId()}`;
    const welcomeKey = `moodle_bot_welcome_${getCourseId()}`;
    const resizeBtn = document.getElementById('moodle-bot-resize-btn');

    let isExpanded = false;
    chatWindow.style.transition = 'width 0.3s ease, height 0.3s ease';

    resizeBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        isExpanded = !isExpanded;

        if (isExpanded) {
            chatWindow.style.width = '600px';
            chatWindow.style.height = '80vh';
            resizeBtn.innerHTML = '🗗';
            resizeBtn.title = 'Уменьшить';
        } else {
            chatWindow.style.width = '';
            chatWindow.style.height = '';
            resizeBtn.innerHTML = '⛶';
            resizeBtn.title = 'Развернуть';
        }

        setTimeout(() => {
            messagesArea.scrollTop = messagesArea.scrollHeight;
        }, 310);
    });

    let savedHistory = sessionStorage.getItem(historyKey);
    if (!savedHistory) {
        savedHistory = `<div class="bot-msg">Привет! Я помощник по этому курсу. Напишите тему, и я подскажу, где это находится.</div>`;
        sessionStorage.setItem(historyKey, savedHistory);
    }
    messagesArea.innerHTML = savedHistory;

    function addMessageToChat(htmlString) {
        messagesArea.innerHTML += htmlString;
        sessionStorage.setItem(historyKey, messagesArea.innerHTML);
        messagesArea.scrollTop = messagesArea.scrollHeight;
    }

    btn.addEventListener('click', () => {
        chatWindow.style.display = chatWindow.style.display === 'flex' ? 'none' : 'flex';
        messagesArea.scrollTop = messagesArea.scrollHeight;
    });

    if (!sessionStorage.getItem(welcomeKey)) {
        setTimeout(() => {
            addMessageToChat(`<div class="bot-msg">Если хотите, я помогу быстро сориентироваться в курсе: можете спросить, где читать нужную тему.</div>`);
            sessionStorage.setItem(welcomeKey, '1');
        }, 800);
    }

    // НОВАЯ ЛОГИКА КЛИКА ПО КНОПКЕ!
    messagesArea.addEventListener('click', (e) => {
        const btn = e.target.closest('.moodle-bot-target-btn');
        if (!btn) return;

        const targetUrl = btn.getAttribute('data-url');
        const targetId = btn.getAttribute('data-id');
        const targetSnippet = btn.getAttribute('data-snippet');

        // Сохраняем сниппет для подсветки
        if (targetSnippet) {
            sessionStorage.setItem('moodle_bot_highlight_text', targetSnippet);
        }

        const currentUrlObj = new URL(window.location.href);
        const targetUrlObj = new URL(targetUrl);

        // Если мы уже на этой странице — просто подсвечиваем и скроллим
        if (
            currentUrlObj.pathname === targetUrlObj.pathname &&
            currentUrlObj.searchParams.get('id') === targetUrlObj.searchParams.get('id')
        ) {
            if (targetSnippet) applyTextHighlight(targetSnippet);
            highlightElement(targetId);
        } else {
            // Если другая страница — телепортируемся!
            sessionStorage.setItem('moodle_bot_teleport_target', targetId);
            window.location.href = targetUrl;
        }
    });

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
            const response = await fetch(`http://127.0.0.1:8000/api/smart-search`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    course_id: getCourseId(),
                    message: text,
                    history: getHistoryForBackend(),
                    viewer_role: getViewerRole(),
                    deadlines: window.courseActiveDeadlines || []
                })
            });

            if (!response.ok) throw new Error("Ошибка сервера");

            const data = await response.json();

            // ПРОСТО ВЫВОДИМ ТЕКСТ И КНОПКИ (никакого авто-редиректа!)
            let formattedReply = data.reply
                .replace(/\n/g, '<br>')
                .replace(/\*\*(.*?)\*\*/g, '<b>$1</b>')
                .replace(/\* /g, '<br>• ');

            let finalHtml = `<div class="bot-msg">${formattedReply}</div>`;

            if (data.targets && data.targets.length > 0) {
                finalHtml += `<div style="margin-top: -5px; margin-bottom: 10px; display: flex; flex-direction: column; gap: 4px;">`;

                const uniqueTargets = Array.from(new Map(data.targets.map(t => [t.id, t])).values());
                uniqueTargets.forEach(t => {
                    finalHtml += `
                        <button class="moodle-bot-target-btn"
                                data-url="${t.url}"
                                data-id="${t.id}"
                                data-snippet="${t.snippet || ''}" 
                                style="text-align: left; padding: 6px 10px; background: #f8f9fa; border: 1px solid #cce5ff; border-radius: 6px; cursor: pointer; color: #004085; font-size: 12px; transition: 0.2s;">
                            🎯 Переход: ${t.title}
                        </button>
                    `;
                });

                finalHtml += `</div>`;
            }

            if (data.debug_context && data.debug_context.length > 0) {
                finalHtml += `
                <details style="margin-top: 8px; font-size: 11px; background: #e9ecef; border-radius: 6px; padding: 5px; border: 1px solid #ced4da;">
                    <summary style="cursor: pointer; color: #495057; font-weight: bold; outline: none;">🔍 Показать источники (Дебаг)</summary>
                    <div style="margin-top: 5px; max-height: 250px; overflow-y: auto; padding-right: 5px;">
                        <div style="margin-bottom: 8px; color: #d63384; font-family: monospace;">
                            <strong>Expanded Query:</strong> ${data.expanded_query || 'НЕТ ДАННЫХ'}
                        </div>`;

                data.debug_context.forEach((ctx, idx) => {
                    finalHtml += `
                        <div style="margin-bottom: 6px; padding-bottom: 6px; border-bottom: 1px solid #dee2e6;">
                            <strong style="color: #0056b3;">[${idx + 1}] ${ctx.title}</strong> 
                            <span style="color: #198754; font-weight: bold;">(Score: ${ctx.score})</span><br>
                            <span style="color: #6c757d; font-family: monospace;">${ctx.text}</span>
                        </div>`;
                });

                finalHtml += `</div></details>`;
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
    inputField.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') sendMessage();
    });
}


// === 3. АВТОМАТИЗАЦИЯ И ZERO-UI ===
async function parseCourseIndex() {
    const courseId = getCourseId();
    if (!courseId || courseId === "unknown") return;

    const doms = await getCourseDOMs();
    const sectionsMap = new Map();

    doms.forEach(doc => {
        doc.querySelectorAll('li.section.main:not(.hidden):not(.section-summary)').forEach(sec => {
            if (!sectionsMap.has(sec.id)) {
                const sectionData = {
                    moodle_id: sec.id,
                    title: sec.querySelector('.sectionname')?.innerText.trim() || "Без названия",
                    modules: []
                };

                sec.querySelectorAll('li.activity').forEach(act => {
                    const a = act.querySelector('a.aalink');
                    const type = getModuleType(act);

                    sectionData.modules.push({
                        moodle_id: act.id,
                        type: type,
                        title: extractModuleTitle(act),
                        url: a ? a.href : null,
                        visibility: extractVisibilityInfo(act)
                    });
                });

                if (sectionData.modules.length > 0) {
                    sectionsMap.set(sec.id, sectionData);
                }
            }
        });
    });

    const courseData = {
        course_id: courseId,
        title: document.title,
        sections: Array.from(sectionsMap.values()),
        viewer_role: getViewerRole()
    };

    try {
        const res = await fetch("http://127.0.0.1:8000/api/course/sync", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(courseData)
        });

        const data = await res.json();
        if (data.needs_initial_sync || getViewerRole() === 'teacher') {
            runSilentSpider();
        }
    } catch (e) {}
}

async function checkMoodleLogForUpdates() {
    const courseId = getCourseId();

    try {
        const res = await fetch(`/report/log/index.php?id=${courseId}&modaction=cud`, {
            credentials: 'include'
        });
        const html = await res.text();
        const doc = new DOMParser().parseFromString(html, "text/html");

        const firstRow = doc.querySelector('#report_log_r0');
        if (!firstRow) return;

        const hash =
            (firstRow.querySelector('.c0')?.innerText || "") + "_" +
            (firstRow.querySelector('.c5')?.innerText || "");

        const savedHash = localStorage.getItem(`moodle_bot_version_${courseId}`);

        if (savedHash !== hash) {
            localStorage.setItem(`moodle_bot_version_${courseId}`, hash);
            runSilentSpider();
        }
    } catch (e) {}
}

async function runSilentSpider() {
    const doms = await getCourseDOMs();
    const validModulesMap = new Map();

    doms.forEach(doc => {
        doc.querySelectorAll('li.activity').forEach(act => {
            const a = act.querySelector('a.aalink');
            const moduleType = getModuleType(act);

            if (a && shouldIndexModuleType(moduleType) && !validModulesMap.has(act.id)) {

                const descEl = act.querySelector('.description .no-overflow, .contentafterlink .no-overflow');
                const inlineDescription = descEl ? cleanText(descEl.innerText) : "";

                validModulesMap.set(act.id, {
                    href: a.href,
                    moodle_id: act.id,
                    module_type: moduleType,
                    title: extractModuleTitle(act),
                    visibility: extractVisibilityInfo(act),
                    inline_desc: inlineDescription,
                    is_file: isFileActivity(act)
                });
            }
        });
    });

    const validModules = Array.from(validModulesMap.values());

    for (let i = 0; i < validModules.length; i += 4) {
        const chunk = validModules.slice(i, i + 4);

        const promises = chunk.map(async (item) => {
            try {
                if (item.is_file) {
                    const fileSeoText = `Это прикрепленный учебный материал (документ, файл или презентация) по теме "${item.title}". Обязательно откройте и изучите этот файл, так как он целиком посвящен теме "${item.title}". ${item.inline_desc || ''}`;
                    return {
                        moodle_id: item.moodle_id,
                        title: item.title,
                        module_type: item.module_type,
                        content_text: cleanText(fileSeoText),
                        url: item.href,
                        visibility: item.visibility
                    };
                }

                let fetchUrl = item.href;
                if (item.module_type === 'book') {
                    fetchUrl = item.href.replace('/view.php', '/tool/print/index.php');
                }

                const response = await fetch(fetchUrl, { credentials: 'include' });

                const contentType = response.headers.get('content-type');
                if (contentType && !contentType.includes('text/html')) {
                    const fileSeoText = `Это загружаемый файл по теме "${item.title}". Он предназначен для изучения темы "${item.title}". ${item.inline_desc || ''}`;
                    return {
                        moodle_id: item.moodle_id,
                        title: item.title,
                        module_type: item.module_type,
                        content_text: cleanText(fileSeoText),
                        url: item.href,
                        visibility: item.visibility
                    };
                }

                const html = await response.text();
                const doc = new DOMParser().parseFromString(html, "text/html");
                let text = extractMeaningfulContent(doc);

                if (item.inline_desc) {
                    text = item.inline_desc + "\n" + text;
                }

                if (!text || text.length < 80) {
                    const hasVideo = doc.querySelector('iframe, video, .mediaplugin');
                    if (hasVideo || item.title.toLowerCase().includes('видео')) {
                        text = cleanText(`Это обучающая видеолекция по теме "${item.title}". Данный медиаматериал целиком и полностью посвящен изучению темы "${item.title}". Обязательно посмотрите это видео, чтобы понять ${item.title}. ${item.inline_desc || ''}`);
                    } else {
                        text = cleanText(`Это практический материал или важная ссылка по теме "${item.title}". Относится к разделу ${item.title}. ${item.inline_desc || ''}`);
                    }
                }

                if (!text) return null;

                return {
                    moodle_id: item.moodle_id,
                    title: item.title,
                    module_type: item.module_type,
                    content_text: text,
                    url: item.href,
                    visibility: item.visibility
                };
            } catch (err) {
                return null;
            }
        });

        const parsedChunk = (await Promise.all(promises)).filter(Boolean);

        if (parsedChunk.length > 0) {
            try {
                await fetch("http://127.0.0.1:8000/api/module/bulk-update", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        course_id: getCourseId(),
                        modules: parsedChunk
                    })
                });
            } catch (error) {
                console.error("Ошибка при отправке чанка на сервер", error);
            }
        }

        await new Promise(r => setTimeout(r, 500));
    }
}

async function passiveModuleSync() {
    const id = new URLSearchParams(window.location.search).get('id');
    const mainContent = document.querySelector('[role="main"], #region-main, body');
    const act = document.querySelector(`li.activity#module-${id}`);
    const moduleType = act ? getModuleType(act) : null;

    if (id && mainContent) {
        try {
            const text = extractMeaningfulContent(document);

            await fetch("http://127.0.0.1:8000/api/module/update", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    course_id: getCourseId(),
                    moodle_id: `module-${id}`,
                    module_type: moduleType,
                    content_text: text,
                    url: window.location.href,
                    visibility: act ? extractVisibilityInfo(act) : { is_hidden: false, has_restrictions: false, raw_text: "" }
                })
            });
        } catch (e) {}
    }
}


// === ТОЧКА ВХОДА ===
setTimeout(async () => {
    injectChatUI();

    // ПРОВЕРЯЕМ ПОДСВЕТКУ ПРИ ЗАГРУЗКЕ (если был редирект)
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
    const isTeacher = isTeacherView();
    const historyKey = `moodle_bot_chat_history_${getCourseId()}`;
    const history = sessionStorage.getItem(historyKey) || "";

    if (path.includes('/course/view.php')) {
        parseCourseIndex();

        if (isTeacher) {
            checkMoodleLogForUpdates();
        }

        const deadlines = await extractDeadlinesFromCourse();
        deadlines
            .filter(d => getDaysLeft(d.due_date) >= 0)
            .sort((a, b) => a.due_date - b.due_date)
            .forEach(showDeadlineNotification);

    } else if (path.includes('/mod/')) {
        passiveModuleSync();

        const messagesArea = document.getElementById('moodle-bot-chat-messages');

        if (path.includes('/mod/assign/') && !history.includes('открыли практическое задание')) {
            messagesArea.innerHTML += `<div class="bot-msg" style="background: #e3f2fd; border-left: 4px solid #007bff;">🎓 Вы открыли практическое задание. Напишите мне, если нужно найти теорию!</div>`;
            sessionStorage.setItem(historyKey, messagesArea.innerHTML);
        } else if (path.includes('/mod/quiz/') && !history.includes('Впереди тест')) {
            messagesArea.innerHTML += `<div class="bot-msg" style="background: #fff3cd; border-left: 4px solid #ffc107;">⚠️ Впереди тест! Убедитесь, что повторили материал. Удачи!</div>`;
            sessionStorage.setItem(historyKey, messagesArea.innerHTML);
        }
    }
}, 1500);