function getCourseId() {
    const match = document.body.className.match(/course-(\d+)/);
    if (match) return match[1];
    const urlParams = new URLSearchParams(window.location.search);
    return urlParams.get('id') || "unknown";
}

function parseRuDate(dateStr) {
    if (!dateStr) return null;
    const normalized = dateStr.trim().toLowerCase().replace(/\s+г\.?$/, '');
    const cleanDate = normalized.replace(/\d{1,2}:\d{2}/, '').trim();
    const patterns = [
        {regex: /^(\d{1,2})\.(\d{1,2})\.(\d{4})$/, handler: (matches) => {
                const day = parseInt(matches[1]);
                const month = parseInt(matches[2]) - 1;
                const year = parseInt(matches[3]);
                return new Date(year, month, day, 23, 59, 0);}
        },
        {regex: /^(\d{1,2})\s+([а-яё]+)\s+(\d{4})$/i, handler: (matches) => {
                const monthMap = {
                    'января': 0, 'февраля': 1, 'марта': 2, 'апреля': 3,
                    'мая': 4, 'июня': 5, 'июля': 6, 'августа': 7,
                    'сентября': 8, 'октября': 9, 'ноября': 10, 'декабря': 11};
                const day = parseInt(matches[1]);
                const month = monthMap[matches[2].toLowerCase()];
                const year = parseInt(matches[3]);
                if (month === undefined) return null;
                return new Date(year, month, day, 23, 59, 0);}
        }
    ];
    for (const pattern of patterns) {
        const matches = cleanDate.match(pattern.regex);
        if (matches) {
            return pattern.handler(matches);
        }
    }
    return null;
}

async function extractDeadlinesFromCourse() {
    const deadlines = [];
    const assignments = document.querySelectorAll('li.activity.modtype_assign a.aalink');
    for (const a of assignments) {
        const link = a.href;
        const title = a.innerText.trim();
        try {
            const response = await fetch(link);
            const htmlText = await response.text();
            const parser = new DOMParser();
            const doc = parser.parseFromString(htmlText, "text/html");
            let submitted = false;
            const statusEl = doc.querySelector('.submissionstatus, .submissionstatussubmitted, .badge-success');
            if(statusEl && /выполнено|отправлено|submitted/i.test(statusEl.innerText)) {
                submitted = true;
            }
            if(submitted) continue;
            const dueTextMatch = doc.body.innerText.match(
                /Срок сдачи:\s*(?:[а-яё]+,\s*)?(\d{1,2}\s+[а-яё]+ \d{4})(?:,\s*\d{2}:\d{2})?/i
            );
            if (dueTextMatch && dueTextMatch[1]) {
                const dueDate = parseRuDate(dueTextMatch[1]);
                if(dueDate){
                    deadlines.push({
                        title,
                        due_date_raw: dueTextMatch[1],
                        due_date: dueDate,
                        url: link,
                        moodle_id: a.closest('li.activity')?.id || null
                    });
                }
            }
        } catch (e) {
            console.error("Ошибка:", link, e);
        }
    }
    return deadlines;
}

function formatDateRu(date) {
    if (!(date instanceof Date) || isNaN(date.getTime())) return '';
    return date.toLocaleDateString('ru-RU');
}

function getDaysLeft(date) {
    const now = new Date();
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const target = new Date(date.getFullYear(), date.getMonth(), date.getDate());
    return Math.ceil((target - today) / (1000*60*60*24));
}

function buildDeadlineLabel(date) {
    const daysLeft = getDaysLeft(date);
    if (daysLeft < 0) return 'Срок уже прошёл';
    if (daysLeft === 0) return 'Сегодня дедлайн';
    if (daysLeft === 1) return 'Завтра дедлайн';
    if (daysLeft <= 3) return `Через ${daysLeft} дн. дедлайн`;
    return 'Ближайшая работа';
}

function markNotificationClosedToday(storageKey) {
    const today = new Date().toLocaleDateString('ru-RU');
    localStorage.setItem(storageKey, today);
}
const minimizedDeadlines = [];
function wasNotificationClosedToday(storageKey) {
    const today = new Date().toLocaleDateString('ru-RU');
    return localStorage.getItem(storageKey) === today;
}
function showDeadlineNotification(deadline) {
    if (!deadline) return;
    const storageKey = `moodle_deadline_closed_${getCourseId()}_${deadline.title}_${deadline.due_date_raw}`;
    if (wasNotificationClosedToday(storageKey)) return;
    const bubbleId = `moodle-deadline-bubble-${deadline.moodle_id || Math.random().toString(36).slice(2)}`;
    const bubble = document.createElement('div');
    bubble.id = bubbleId;
    bubble.classList.add('moodle-deadline-bubble');

    const badgeText = buildDeadlineLabel(deadline.due_date);
    const prettyDate = formatDateRu(deadline.due_date);
    bubble.innerHTML = `
        <div class="moodle-deadline-badge">${badgeText}</div>
         <div class="moodle-deadline-title"><span class="reminder">Напоминание</span></div>
        <div class="moodle-deadline-text">
            Прикрепите работу <b>${deadline.title}</b> до <b>${prettyDate}</b>
        </div>
        <div class="moodle-deadline-actions">
            <button class="moodle-deadline-open">Открыть</button>
            <button class="moodle-deadline-minimize" title="Свернуть">_</button>
            <button class="moodle-deadline-close" title="Закрыть">×</button>
        </div>
    `;
    const container = document.getElementById('moodle-deadlines-container');
    container.appendChild(bubble);

    bubble.querySelector('.moodle-deadline-open')?.addEventListener('click', () => {
        if (deadline.moodle_id) {
            const element = document.getElementById(deadline.moodle_id);
            if (element) element.scrollIntoView({behavior:'smooth', block:'center'});
        }
        if (deadline.url) window.open(deadline.url, '_blank');
    });

    bubble.querySelector('.moodle-deadline-minimize')?.addEventListener('click', () => {
        bubble.style.display = 'none';
        minimizedDeadlines.push(deadline);
        updateMinimizedChatButton();
    });

    bubble.querySelector('.moodle-deadline-close')?.addEventListener('click', () => {
        markNotificationClosedToday(storageKey);
        bubble.classList.add('moodle-deadline-close-animation');
        setTimeout(() => bubble.remove(), 400);
    });
}

function updateMinimizedChatButton() {
    let chatBtn = document.getElementById('moodle-minimized-btn');
    if (!chatBtn) {
        chatBtn = document.createElement('button');
        chatBtn.id = 'moodle-minimized-btn';

        const video = document.createElement('video');
        video.src = "https://cdn-icons-mp4.flaticon.com/512/11919/11919421.mp4";
        video.autoplay = true;
        video.loop = true;
        video.muted = true;
        video.playsInline = true;
        chatBtn.appendChild(video);
        document.body.appendChild(chatBtn);
        chatBtn.addEventListener('click', () => {
            minimizedDeadlines.forEach(dl => showDeadlineNotification(dl));
            minimizedDeadlines.length = 0;
            chatBtn.remove();
        });
    }
}

function highlightElement(targetId) {
    const element = document.getElementById(targetId);
    if (element) {
        element.scrollIntoView({ behavior: 'smooth', block: 'center' });
        element.classList.add('bot-highlight-animation');
        setTimeout(() => element.classList.remove('bot-highlight-animation'), 4000);
    }
}

function injectChatUI() {
    if (!document.getElementById('moodle-deadlines-container')) {
        const container = document.createElement('div');
        container.id = 'moodle-deadlines-container';
        document.body.appendChild(container);
    }

    const btn = document.createElement('div');
    btn.id = 'moodle-bot-btn';
    btn.innerHTML = '🤖';
    document.body.appendChild(btn);

    const chatWindow = document.createElement('div');
    chatWindow.id = 'moodle-bot-chat';
    chatWindow.innerHTML = `
        <div id="moodle-bot-chat-header">Moodle Assistant</div>
        <div id="moodle-bot-chat-messages">
            <div class="bot-msg">Привет! Я цифровой наставник этого курса. Что ищем?</div>
        </div>
        <div id="moodle-bot-chat-input-area">
            <input type="text" id="moodle-bot-chat-input" placeholder="Введите ваш вопрос...">
            <button id="moodle-bot-chat-send">▶</button>
        </div>
    `;
    document.body.appendChild(chatWindow);

    const messagesArea = document.getElementById('moodle-bot-chat-messages');
    const sendBtn = document.getElementById('moodle-bot-chat-send');
    const inputField = document.getElementById('moodle-bot-chat-input');

    const isTeacher = document.querySelector('form[action*="editmode.php"]') !== null;
    const isMainPage = window.location.pathname.includes('/course/view.php');

    if (isTeacher && isMainPage) {
        messagesArea.innerHTML += `
            <div class="bot-msg" style="border: 2px solid #ffc107; background: #fffdf5;">
                <b>Режим преподавателя 🎓</b><br>
                <button id="moodle-bot-spider-btn" class="btn btn-sm btn-warning mt-2" style="cursor:pointer; width:100%;">🕷️ Индексировать курс</button>
            </div>
        `;

        setTimeout(() => {
            const spiderBtn = document.getElementById('moodle-bot-spider-btn');
            if(spiderBtn) spiderBtn.addEventListener('click', () => runSpider(spiderBtn, messagesArea));
        }, 500);
    }

    btn.addEventListener('click', () => {
        chatWindow.style.display = chatWindow.style.display === 'flex' ? 'none' : 'flex';
        messagesArea.scrollTop = messagesArea.scrollHeight;
    });

    let isSending = false;

    const sendMessage = async () => {
        if (isSending) return;

        const text = inputField.value.trim();
        if (!text) return;

        isSending = true;
        sendBtn.style.opacity = "0.5";
        inputField.disabled = true;

        messagesArea.innerHTML += `<div class="user-msg">${text}</div>`;
        inputField.value = '';
        messagesArea.scrollTop = messagesArea.scrollHeight;

        const courseId = getCourseId();

        try {
            const response = await fetch(`http://127.0.0.1:8000/api/smart-search`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ course_id: courseId, message: text })
            });

            if (!response.ok) {
                console.error("Ошибка от сервера:", await response.text());
                throw new Error(`Ошибка сервера: ${response.status}`);
            }

            const data = await response.json();

            messagesArea.innerHTML += `<div class="bot-msg">${data.reply}</div>`;
            messagesArea.scrollTop = messagesArea.scrollHeight;

            if (data.target_id) highlightElement(data.target_id);

        } catch (error) {
            messagesArea.innerHTML += `<div class="bot-msg" style="color:red;">Связь с сервером потеряна или запрос отклонен. Проверьте консоль!</div>`;
            messagesArea.scrollTop = messagesArea.scrollHeight;
        }

        setTimeout(() => {
            isSending = false;
            sendBtn.style.opacity = "1";
            inputField.disabled = false;
            inputField.focus();
        }, 1500);
    };

    sendBtn.addEventListener('click', sendMessage);
    inputField.addEventListener('keypress', (e) => { if (e.key === 'Enter') sendMessage(); });
    if (isMainPage) {
        setTimeout(async () => {
            const deadlines = await extractDeadlinesFromCourse();
            const upcoming = deadlines
                .filter(d => d.due_date instanceof Date && !isNaN(d.due_date.getTime()))
                .filter(d => getDaysLeft(d.due_date) >= 0)
                .sort((a,b) => a.due_date - b.due_date);
            for (const d of upcoming) {
                showDeadlineNotification(d);
            }
        }, 0);
    }
}

function proactiveGreeting() {
    const messagesArea = document.getElementById('moodle-bot-chat-messages');
    if (!messagesArea) return;
    const path = window.location.pathname;

    if (path.includes('/mod/assign/view.php')) {
        messagesArea.innerHTML += `<div class="bot-msg" style="background: #e3f2fd; border-left: 4px solid #007bff;">🎓 Вижу, вы открыли практическое задание. Помните: чтобы успешно его сдать, нужно опираться на теорию. Напишите мне, если нужно найти соответствующую лекцию!</div>`;
    } else if (path.includes('/mod/quiz/view.php')) {
        messagesArea.innerHTML += `<div class="bot-msg" style="background: #fff3cd; border-left: 4px solid #ffc107;">⚠️ Впереди тестирование! Убедитесь, что повторили все материалы. Удачи!</div>`;
    }
}

async function parseCourseIndex() {
    const courseId = getCourseId();
    if (!courseId || courseId === "unknown") return;

    const courseTitle = document.querySelector('.headermain')?.innerText.trim() || document.title;
    const courseData = { course_id: courseId, title: courseTitle, sections: [] };

    document.querySelectorAll('li.section.main').forEach(sec => {
        if (sec.classList.contains('hidden')) return;
        const sectionData = { moodle_id: sec.id, title: sec.querySelector('.sectionname')?.innerText.trim() || "Без названия", modules: [] };

        sec.querySelectorAll('li.activity').forEach(act => {
            const linkNode = act.querySelector('a.aalink');
            const instanceNameNode = act.querySelector('.instancename');
            let title = "Без названия";
            if (instanceNameNode) {
                const clone = instanceNameNode.cloneNode(true);
                const hiddenSpan = clone.querySelector('.accesshide');
                if (hiddenSpan) hiddenSpan.remove();
                title = clone.innerText.trim();
            }
            let type = act.className.match(/modtype_(\w+)/) ? act.className.match(/modtype_(\w+)/)[1] : "unknown";
            sectionData.modules.push({ moodle_id: act.id, type: type, title: title, url: linkNode ? linkNode.href : null });
        });
        if (sectionData.modules.length > 0) courseData.sections.push(sectionData);
    });

    try {
        await fetch("http://127.0.0.1:8000/api/course/sync", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(courseData)
        });
    } catch (e) {}
}

async function passiveModuleSync() {
    const urlParams = new URLSearchParams(window.location.search);
    const moduleIdRaw = urlParams.get('id');
    if (!moduleIdRaw) return;

    const fullModuleId = `module-${moduleIdRaw}`;
    const mainContent = document.querySelector('[role="main"]');

    if (mainContent) {
        const textContent = mainContent.innerText.replace(/\s+/g, ' ').trim();
        try {
            await fetch("http://127.0.0.1:8000/api/module/update", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ course_id: getCourseId(), moodle_id: fullModuleId, content_text: textContent, url: window.location.href })
            });
        } catch (e) {}
    }
}

async function runSpider(btnElement, messagesArea) {
    btnElement.disabled = true;
    btnElement.innerText = "⏳ Собираю ссылки...";

    const links = [];
    document.querySelectorAll('li.activity.modtype_page a.aalink, li.activity.modtype_resource a.aalink, li.activity.modtype_assign a.aalink, li.activity.modtype_book a.aalink').forEach(a => links.push(a.href));

    const total = links.length;
    const courseId = getCourseId();
    messagesArea.innerHTML += `<div class="bot-msg">Найдено ссылок для парсинга: ${total}. Начинаю работу...</div>`;
    messagesArea.scrollTop = messagesArea.scrollHeight;

    for (let i = 0; i < total; i++) {
        btnElement.innerText = `🕷️ ${i + 1} из ${total}`;
        try {
            const response = await fetch(links[i]);
            const htmlText = await response.text();
            const doc = new DOMParser().parseFromString(htmlText, "text/html");
            const main = doc.querySelector('[role="main"]');

            if (main) {
                const urlObj = new URL(links[i]);
                await fetch("http://127.0.0.1:8000/api/module/update", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ course_id: courseId, moodle_id: `module-${urlObj.searchParams.get('id')}`, content_text: main.innerText.replace(/\s+/g, ' ').trim(), url: links[i] })
                });
            }
        } catch (err) {}
        await new Promise(r => setTimeout(r, 1000));
    }

    btnElement.innerText = "✅ База обновлена!";
    btnElement.classList.replace('btn-warning', 'btn-success');
    messagesArea.innerHTML += `<div class="bot-msg"><b>Готово!</b> Весь контент сохранен в векторную базу.</div>`;
    messagesArea.scrollTop = messagesArea.scrollHeight;
}

setTimeout(() => {
    injectChatUI();
    const path = window.location.pathname;

    if (path.includes('/course/view.php')) {
        parseCourseIndex();
    } else if (path.includes('/mod/')) {
        passiveModuleSync();
        proactiveGreeting();
    }
}, 1500);