async function parseAndSyncCourse() {
    const urlParams = new URLSearchParams(window.location.search);
    const courseId = urlParams.get('id');

    if (!courseId) return;

    const courseTitle = document.querySelector('.headermain')?.innerText.trim() || document.title;
    const courseData = { course_id: courseId, title: courseTitle, sections: [] };

    const sections = document.querySelectorAll('li.section.main');

    sections.forEach(sec => {
        if (sec.classList.contains('hidden')) return;

        const sectionData = {
            moodle_id: sec.id,
            title: sec.querySelector('.sectionname')?.innerText.trim() || "Без названия",
            modules: []
        };

        sec.querySelectorAll('li.activity').forEach(act => {
            const linkNode = act.querySelector('a.aalink');

            // Чистим заголовок
            const instanceNameNode = act.querySelector('.instancename');
            let title = "Без названия";
            if (instanceNameNode) {
                const clone = instanceNameNode.cloneNode(true);
                const hiddenSpan = clone.querySelector('.accesshide');
                if (hiddenSpan) hiddenSpan.remove();
                title = clone.innerText.trim();
            }

            let type = "unknown";
            if (act.classList.contains('modtype_assign')) type = "assignment";
            else if (act.classList.contains('modtype_quiz')) type = "quiz";
            else if (act.classList.contains('modtype_resource')) type = "file";
            else if (act.classList.contains('modtype_page')) type = "page";
            else if (act.classList.contains('modtype_forum')) type = "forum";
            else if (act.classList.contains('modtype_book')) type = "book";
            else if (act.classList.contains('modtype_scorm')) type = "scorm";
            else if (act.classList.contains('modtype_url')) type = "url";

            sectionData.modules.push({
                moodle_id: act.id,
                type: type,
                title: title,
                url: linkNode ? linkNode.href : null
            });
        });

        if (sectionData.modules.length > 0) courseData.sections.push(sectionData);
    });

    console.log("Парсинг завершен. Отправляю на бэкенд...", courseData);

    // ОТПРАВЛЯЕМ ДАННЫЕ НА НАШ ПИТОН-СЕРВЕР
    try {
        const response = await fetch("http://127.0.0.1:8000/api/course/sync", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(courseData)
        });
        const result = await response.json();
        console.log("Ответ от сервера:", result);
    } catch (error) {
        console.error("Ошибка синхронизации с бэкендом:", error);
    }
}

setTimeout(parseAndSyncCourse, 2000);