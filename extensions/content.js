function getCourseId() {
    const match = document.body.className.match(/course-(\d+)/);
    if (match) return match[1];
    const urlParams = new URLSearchParams(window.location.search);
    return urlParams.get('id') || "unknown";
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

            // Превращаем переносы строк в теги <br>, а **текст** в жирный
            const formattedReply = data.reply
                .replace(/\n/g, '<br>')
                .replace(/\*\*(.*?)\*\*/g, '<b>$1</b>')
                .replace(/\* /g, '• '); // Заменяем звездочки списков на красивые точки

            messagesArea.innerHTML += `<div class="bot-msg">${formattedReply}</div>`;
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
    checkFirstVisitAndGreet(courseData);

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

function checkFirstVisitAndGreet(courseData) {
    // Создаем уникальный ключ для этого курса
    const visitedKey = `moodle_bot_visited_${courseData.course_id}`;

    // Проверяем, заходил ли студент сюда ранее
    if (!localStorage.getItem(visitedKey)) {

        // Считаем количество заданий и тестов
        let assignCount = 0;
        let quizCount = 0;

        courseData.sections.forEach(sec => {
            sec.modules.forEach(mod => {
                if (mod.type === 'assignment') assignCount++;
                if (mod.type === 'quiz') quizCount++;
            });
        });

        const chatWindow = document.getElementById('moodle-bot-chat');
        const messagesArea = document.getElementById('moodle-bot-chat-messages');
        const botBtn = document.getElementById('moodle-bot-btn');

        if (chatWindow && messagesArea && botBtn) {
            // Принудительно открываем чат
            chatWindow.style.display = 'flex';

            // Заставляем кнопку пульсировать для привлечения внимания
            botBtn.classList.add('bot-highlight-animation');
            setTimeout(() => botBtn.classList.remove('bot-highlight-animation'), 4000);

            // Формируем и отправляем сообщение от бота
            messagesArea.innerHTML += `
                <div class="bot-msg" style="border: 2px solid #007bff; background: #e2eef9;">
                    👋 <b>Добро пожаловать на курс!</b><br>
                    Я проанализировал оглавление <i>"${courseData.title}"</i>.<br><br>
                    В этом курсе тебя ждут:<br>
                    📝 Заданий: <b>${assignCount}</b><br>
                    ❓ Тестов: <b>${quizCount}</b><br><br>
                    Пока я не нашел горящих дедлайнов. Я буду следить за обновлениями. Если нужно найти конкретную работу — просто напиши мне!
                </div>
            `;
            messagesArea.scrollTop = messagesArea.scrollHeight;

            // Запоминаем, что мы уже поздоровались
            localStorage.setItem(visitedKey, 'true');
        }
    }
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