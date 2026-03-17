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
                const assignDoc = new DOMParser().parseFromString(await response.text(), "text/html");
                const statusEl = assignDoc.querySelector('.submissionstatus, .submissionstatussubmitted, .badge-success');
                if (statusEl && /выполнено|отправлено|submitted/i.test(statusEl.innerText)) continue;

                const dueTextMatch = assignDoc.body.innerText.match(/Срок сдачи:\s*(?:[а-яё]+,\s*)?(\d{1,2}\s+[а-яё]+ \d{4})/i);
                if (dueTextMatch && dueTextMatch[1]) {
                    const dueDate = parseRuDate(dueTextMatch[1]);
                    if (dueDate) {
                        deadlines.push({ title: a.innerText.trim(), due_date: dueDate, url: a.href, moodle_id: act.id });
                    }
                }
            } catch (e) {}
        }
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
    if (sessionStorage.getItem(storageKey) === 'collapsed') {
        widget.classList.add('is-collapsed');
    }

    const header = document.createElement('div');
    header.id = 'moodle-deadlines-header';
    header.title = "Нажмите, чтобы развернуть/свернуть";

    const badgeHtml = processed.length > 0
        ? `<span class="md-badge" title="Ожидают сдачи">${processed.length}</span>`
        : `<span class="md-badge" style="background:#28a745;" title="Всё сдано">✓</span>`;

    header.innerHTML = `
        <span class="md-icon">📅</span>
        <span class="md-title">Задания и Дедлайны</span>
        ${badgeHtml}
        <button class="md-toggle-btn">${widget.classList.contains('is-collapsed') ? '◀' : '▼'}</button>
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

    header.addEventListener('click', () => {
        widget.classList.toggle('is-collapsed');
        sessionStorage.setItem(storageKey, widget.classList.contains('is-collapsed') ? 'collapsed' : 'expanded');
        const btn = header.querySelector('.md-toggle-btn');
        btn.innerHTML = widget.classList.contains('is-collapsed') ? '◀' : '▼';
    });
}

function toggleDeadlinesVisibility(hide) {
    const widget = document.getElementById('moodle-deadlines-widget');
    if (widget) {
        widget.style.display = hide ? 'none' : 'block';
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
    const btn = document.createElement('div');
    btn.id = 'moodle-bot-btn';
    btn.innerHTML = `<img src="${chrome.runtime.getURL('assets/logo.png')}" alt="Moodle Bot">`;
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
    const closeBtn = document.getElementById('moodle-bot-close-btn');

    let isExpanded = false;
    let isChatOpen = false;

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
        toggleDeadlinesVisibility(false); // Показываем дедлайны
    });

    btn.addEventListener('click', () => {
        spinCircle();
        isChatOpen = !isChatOpen;
        chatWindow.classList.toggle('is-open', isChatOpen);
        btn.classList.toggle('is-active', isChatOpen);

        toggleDeadlinesVisibility(isChatOpen); // Скрываем дедлайны, если открыт чат

        if (isChatOpen) {
            setTimeout(() => {
                messagesArea.scrollTop = messagesArea.scrollHeight;
                inputField.focus();
            }, 180);
        }
    });

    messagesArea.innerHTML = sessionStorage.getItem(historyKey) || `<div class="bot-msg">Привет! Я помощник по этому курсу. Напишите тему, и я подскажу, где это находится.</div>`;

    window.MoodleBot.addMessageToChat = function(htmlString) {
        messagesArea.innerHTML += htmlString;
        sessionStorage.setItem(historyKey, messagesArea.innerHTML);
        messagesArea.scrollTop = messagesArea.scrollHeight;
    };

    // Старый блок if (!sessionStorage.getItem(welcomeKey)) полностью удаляем,
    // так как теперь у нас будет умный онбординг.

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
        toggleDeadlinesVisibility(true); // Скрываем дедлайны при автооткрытии чата после телепортации
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
            const response = await fetch(`http://127.0.0.1:8000/api/smart-search`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    course_id: getCourseId(),
                    message: text,
                    history: getHistoryForBackend(),
                    viewer_role: getViewerRole(),
                    deadlines: window.MoodleBot.activeDeadlines || []
                })
            });

            if (!response.ok) throw new Error("Ошибка сервера");
            const data = await response.json();

            let finalHtml = `<div class="bot-msg">${data.reply.replace(/\n/g, '<br>').replace(/\*\*(.*?)\*\*/g, '<b>$1</b>').replace(/\* /g, '<br>• ')}</div>`;

            if (data.targets && data.targets.length > 0) {
                finalHtml += `<div style="display: flex; flex-direction: column; gap: 4px;">`;
                data.targets.forEach(t => {
                    finalHtml += `<button class="moodle-bot-target-btn" data-url="${t.url}" data-id="${t.id}" data-snippet="${t.snippet || ''}">🎯 Переход: ${t.title}</button>`;
                });
                finalHtml += `</div>`;
            }

            if (data.debug_context && data.debug_context.length > 0) {
                finalHtml += `<details class="moodle-debug-panel"><summary>🔍 Показать источники (Дебаг)</summary><div><div class="moodle-debug-query"><strong>Expanded Query:</strong> ${data.expanded_query || 'НЕТ ДАННЫХ'}</div>`;
                data.debug_context.forEach((ctx, idx) => {
                    finalHtml += `<div class="moodle-debug-item"><strong>[${idx + 1}] ${ctx.title}</strong> <span>(Score: ${ctx.score})</span><br><p>${ctx.text}</p></div>`;
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
    inputField.addEventListener('keypress', (e) => { if (e.key === 'Enter') sendMessage(); });
}

async function getUngradedAssignments() {
    const doms = await getCourseDOMs();
    const assignments = [];
    const seenAssigns = new Set();

    // 1. Собираем все практические задания на курсе
    doms.forEach(doc => {
        doc.querySelectorAll('li.activity.modtype_assign').forEach(act => {
            if (seenAssigns.has(act.id)) return;
            seenAssigns.add(act.id);

            const a = act.querySelector('a.aalink');
            if (a) assignments.push({ id: act.id, title: extractModuleTitle(act), url: a.href });
        });
    });

    const ungradedList = [];
    let totalUngraded = 0;

    // 2. Делаем запросы пачками (по 3 штуки), чтобы не положить сервер Moodle
    for (let i = 0; i < assignments.length; i += 3) {
        const chunk = assignments.slice(i, i + 3);
        const promises = chunk.map(async (assign) => {
            try {
                const response = await fetch(assign.url, { credentials: 'include' });
                const html = await response.text();

                // Ищем строчку "Требуют оценки" или "Needs grading" и число после нее
                const doc = new DOMParser().parseFromString(html, "text/html");
                const pageText = doc.body.innerText;

                // Регулярка захватывает число, стоящее рядом со словами "Требуют оценки"
                const match = pageText.match(/(?:Требуют оценки|Needs grading)[\s\n]*(\d+)/i);

                if (match && parseInt(match[1]) > 0) {
                    const count = parseInt(match[1]);
                    totalUngraded += count;
                    ungradedList.push({
                        title: assign.title,
                        url: assign.url + '&action=grading', // Сразу ведем на страницу оценки!
                        count: count
                    });
                }
            } catch (e) {}
        });
        await Promise.all(promises);
    }

    // Сортируем так, чтобы задания с бОльшим числом долгов были сверху
    return ungradedList.sort((a, b) => b.count - a.count);
}

// === ТОЧКА ВХОДА ===
setTimeout(async () => {
    injectChatUI();

    // Проверяем, не перекинул ли нас ИИ на эту страницу
    const targetId = sessionStorage.getItem('moodle_bot_teleport_target');
    if (targetId) {
        setTimeout(() => highlightElement(targetId), 1000);
        sessionStorage.removeItem('moodle_bot_teleport_target');
    }

    // Проверяем, есть ли текст для умной Notion-подсветки
    const textToHighlight = sessionStorage.getItem('moodle_bot_highlight_text');
    if (textToHighlight) {
        sessionStorage.removeItem('moodle_bot_highlight_text');
        applyTextHighlight(textToHighlight);
    }

    const path = window.location.pathname;

    // Если мы на главной странице курса
    if (path.includes('/course/view.php')) {
        parseCourseIndex();

        // Отрисовка Центра Дедлайнов
        const deadlines = await extractDeadlinesFromCourse();
        renderDeadlinesWidget(deadlines);

        // === ПРОАКТИВНЫЕ ФИЧИ ДЛЯ СТУДЕНТА ===
        if (getViewerRole() === 'student') {
            const courseId = getCourseId();

            // --- ПУНКТ 1: ПРОАКТИВНЫЙ ОНБОРДИНГ ---
            const onboardingKey = `moodle_bot_onboarded_${courseId}`;
            if (!localStorage.getItem(onboardingKey)) {
                let quizCount = 0;
                let assignCount = 0;

                const doms = await getCourseDOMs();
                doms.forEach(doc => {
                    const quizzes = Array.from(doc.querySelectorAll('li.activity.modtype_quiz'));
                    const assigns = Array.from(doc.querySelectorAll('li.activity.modtype_assign'));

                    quizCount += quizzes.filter(el => !isActivityHidden(el)).length;
                    assignCount += assigns.filter(el => !isActivityHidden(el)).length;
                });

                let welcomeMsg = `Привет! 👋 Я твой ИИ-помощник по этому курсу. Давай посмотрим, что нас ждет впереди:<br><br>`;
                welcomeMsg += `📝 <b>Практических заданий:</b> ${assignCount}<br>`;
                welcomeMsg += `🧠 <b>Тестов:</b> ${quizCount}<br><br>`;

                const processedDeadlines = deadlines.map(d => ({
                    ...d,
                    daysLeft: getDaysLeft(parseRuDate(d.due_date))
                })).filter(d => d.daysLeft >= 0).sort((a, b) => a.daysLeft - b.daysLeft);

                if (processedDeadlines.length > 0) {
                    const nearest = processedDeadlines[0];
                    welcomeMsg += `🚨 <b>Ближайший дедлайн:</b> ${nearest.title} (до ${nearest.due_date}).<br>`;
                    welcomeMsg += `<button class="moodle-bot-target-btn" data-url="${nearest.url}" style="margin-top: 5px; width: 100%; background: #e3f2fd; border: 1px solid #90caf9; border-radius: 6px; cursor: pointer; color: #004085; font-size: 13px; padding: 6px;">Перейти к заданию</button><br>`;
                } else {
                    welcomeMsg += `🎉 <b>Горящих дедлайнов пока нет.</b> Можно спокойно изучать теорию.<br><br>`;
                }

                welcomeMsg += `Если нужно найти какую-то лекцию, теорию или требования — просто напиши мне!`;

                setTimeout(() => {
                    if (window.MoodleBot && window.MoodleBot.addMessageToChat) {
                        window.MoodleBot.addMessageToChat(`<div class="bot-msg">${welcomeMsg}</div>`);
                        const chatWindow = document.getElementById('moodle-bot-chat');
                        const botBtn = document.getElementById('moodle-bot-btn');
                        if (chatWindow && botBtn && !chatWindow.classList.contains('is-open')) {
                            botBtn.click();
                        }
                    }
                }, 1000);

                localStorage.setItem(onboardingKey, 'true');
            }

            // --- ПУНКТ 3: ДЕТЕКТОР НОВЫХ МАТЕРИАЛОВ ---
            const storageKey = `moodle_bot_known_modules_${courseId}`;
            const knownModulesStr = localStorage.getItem(storageKey);

            const currentModules = [];
            const doms = await getCourseDOMs();

            doms.forEach(doc => {
                doc.querySelectorAll('li.activity').forEach(act => {
                    const a = act.querySelector('a.aalink');
                    if (!isActivityHidden(act) && a) {
                        const title = extractModuleTitle(act);
                        if (title) {
                            currentModules.push({ id: act.id, title: title, url: a.href });
                        }
                    }
                });
            });

            if (knownModulesStr) {
                const knownModuleIds = new Set(JSON.parse(knownModulesStr));
                const newModules = currentModules.filter(m => !knownModuleIds.has(m.id));

                if (newModules.length > 0) {
                    setTimeout(() => {
                        let msg = `🔔 <b>На курсе появились новые материалы!</b><br>Преподаватель добавил или открыл доступ к новым разделам с вашего последнего визита:<br><br>`;

                        const displayModules = newModules.slice(0, 3);
                        let buttonsHtml = '<div style="display: flex; flex-direction: column; gap: 4px;">';

                        displayModules.forEach(m => {
                            buttonsHtml += `<button class="moodle-bot-target-btn" data-url="${m.url}" data-id="${m.id}" style="text-align: left; padding: 6px 10px; background: #e3f2fd; border: 1px solid #90caf9; border-radius: 6px; cursor: pointer; color: #004085; font-size: 12px; transition: 0.2s;">🆕 Открыть: ${m.title}</button>`;
                        });
                        buttonsHtml += '</div>';

                        msg += buttonsHtml;

                        if (newModules.length > 3) {
                            msg += `<div style="font-size: 11px; color: #6c757d; margin-top: 6px;">И еще ${newModules.length - 3} других материалов...</div>`;
                        }

                        if (window.MoodleBot && window.MoodleBot.addMessageToChat) {
                            window.MoodleBot.addMessageToChat(`<div class="bot-msg" style="border-left: 4px solid #0d6efd;">${msg}</div>`);

                            const chatWindow = document.getElementById('moodle-bot-chat');
                            const botBtn = document.getElementById('moodle-bot-btn');
                            if (chatWindow && botBtn && !chatWindow.classList.contains('is-open')) {
                                botBtn.click();
                            }
                        }
                    }, 2500); // Показываем чуть позже, чтобы не перебивать интерфейс
                }
            }

            // Обновляем слепок: сохраняем текущие ID в localStorage
            const currentIds = currentModules.map(m => m.id);
            localStorage.setItem(storageKey, JSON.stringify(currentIds));

        } else if (getViewerRole() === 'teacher') {
            const courseId = getCourseId();
            const teacherKey = `moodle_bot_teacher_notified_${courseId}`;

            // Делаем слепок по дате, чтобы уведомлять 1 раз в день, а не при каждом F5
            const today = new Date().toLocaleDateString('ru-RU');

            if (localStorage.getItem(teacherKey) !== today) {
                // Запускаем нашу ищейку
                getUngradedAssignments().then(ungradedList => {
                    if (ungradedList.length > 0) {
                        const totalCount = ungradedList.reduce((sum, item) => sum + item.count, 0);

                        let msg = `🎓 <b>Приветствую, коллега!</b><br>У вас накопились непроверенные работы: <b>${totalCount} шт.</b><br><br>`;

                        let buttonsHtml = '<div style="display: flex; flex-direction: column; gap: 4px;">';

                        ungradedList.forEach(item => {
                            buttonsHtml += `<button class="moodle-bot-target-btn" data-url="${item.url}" style="text-align: left; padding: 6px 10px; background: #f8f9fa; border: 1px solid #ced4da; border-left: 4px solid #198754; border-radius: 6px; cursor: pointer; color: #212529; font-size: 12px; transition: 0.2s;">
                                📄 ${item.title} <span style="float: right; font-weight: bold; color: #198754;">+${item.count}</span>
                            </button>`;
                        });
                        buttonsHtml += '</div>';

                        msg += buttonsHtml;
                        msg += `<br><span style="font-size: 11px; color: #6c757d;">Нажмите на задание, чтобы сразу перейти к выставлению оценок.</span>`;

                        setTimeout(() => {
                            if (window.MoodleBot && window.MoodleBot.addMessageToChat) {
                                window.MoodleBot.addMessageToChat(`<div class="bot-msg" style="border-left: 4px solid #198754; background: #f8fff9;">${msg}</div>`);

                                const chatWindow = document.getElementById('moodle-bot-chat');
                                const botBtn = document.getElementById('moodle-bot-btn');
                                if (chatWindow && botBtn && !chatWindow.classList.contains('is-open')) {
                                    botBtn.click();
                                }
                            }
                            // Сохраняем дату, чтобы сегодня больше не спамить
                            localStorage.setItem(teacherKey, today);
                        }, 2000);
                    } else {
                        // Если проверять нечего, тихо радуем препода (чат открывать не будем)
                        if (window.MoodleBot && window.MoodleBot.addMessageToChat) {
                            window.MoodleBot.addMessageToChat(`<div class="bot-msg" style="color: #198754;">🎉 Все работы проверены! Вы восхитительны.</div>`);
                        }
                        localStorage.setItem(teacherKey, today);
                    }
                });
            }
        }

    } else if (path.includes('/mod/')) {
        passiveModuleSync(); // Если мы внутри лекции/задания, тихо синхронизируем данные
    }
}, 1500);