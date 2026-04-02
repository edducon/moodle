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
                    const moduleType = getModuleType(act);

                    const urlToSave = a ? a.href : window.location.origin + window.location.pathname + '#' + act.id;

                    sectionData.modules.push({
                        moodle_id: act.id,
                        type: moduleType,
                        title: moduleType === 'label' ? 'Текстовый блок' : extractModuleTitle(act),
                        url: urlToSave,
                        visibility: extractVisibilityInfo(act)
                    });
                });

                if (sectionData.modules.length > 0) {
                    sectionsMap.set(sec.id, sectionData);
                }
            }
        });
    });

    try {
        const teachersPromise = parseTeachersFromParticipants();
        const teachers = await teachersPromise;

        const res = await fetch("http://127.0.0.1:8000/api/course/sync", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                course_id: courseId,
                title: document.title,
                sections: Array.from(sectionsMap.values()),
                viewer_role: getViewerRole(),
                participants: teachers
            })
        });

        const data = await res.json();
        if (data.needs_initial_sync || getViewerRole() === 'teacher') {
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
            const isLabel = moduleType === 'label';

            if ((a || isLabel) && shouldIndexModuleType(moduleType) && !validModulesMap.has(act.id)) {
                const visibilityData = extractVisibilityInfo(act);
                validModulesMap.set(act.id, {
                    href: a ? a.href : window.location.origin + window.location.pathname + '#' + act.id,
                    moodle_id: act.id,
                    module_type: moduleType,
                    title: isLabel ? 'Текстовый блок (пояснение)' : extractModuleTitle(act),
                    visibility: visibilityData,
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
                let metaContext = [];
                const vis = item.visibility || {};

                if (vis.section_title) metaContext.push("Тема/Раздел курса: " + vis.section_title);
                if (vis.dates && vis.dates.length > 0) metaContext.push("Даты/Сроки: " + vis.dates.join("; "));
                if (vis.restrictions && vis.restrictions.length > 0) metaContext.push("Ограничения доступа: " + vis.restrictions.join("; "));
                if (vis.completion_rules && vis.completion_rules.length > 0) metaContext.push("Условия завершения элемента: " + vis.completion_rules.join("; "));
                if (vis.resource_details) metaContext.push("Параметры файла: " + vis.resource_details);
                if (vis.inline_desc) metaContext.push("Описание на странице: " + vis.inline_desc);

                const metaString = metaContext.length > 0 ? ("\nМЕТАДАННЫЕ ЭЛЕМЕНТА:\n" + metaContext.join("\n")) : "";

                if (item.is_file) {
                    const fileSeoText = `Это прикрепленный учебный материал (файл) по теме "${item.title}". Обязательно изучите этот файл.` + metaString;
                    return { ...item, content_text: cleanText(fileSeoText), url: item.href };
                }

                if (item.module_type === 'label') {
                    const labelText = `Это текстовая вставка (пояснение) на странице курса.` + metaString;
                    return { ...item, content_text: cleanText(labelText), url: item.href };
                }

                let fetchUrl = item.href;
                if (item.module_type === 'book') {
                    fetchUrl = item.href.replace('/view.php', '/tool/print/index.php');
                }

                const response = await fetch(fetchUrl, { credentials: 'include' });
                const contentType = response.headers.get('content-type');

                if (contentType && !contentType.includes('text/html')) {
                    const fileSeoText = `Это загружаемый файл по теме "${item.title}".` + metaString;
                    return { ...item, content_text: cleanText(fileSeoText), url: item.href };
                }

                const doc = new DOMParser().parseFromString(await response.text(), "text/html");
                let text = extractMeaningfulContent(doc);

                if (!text || text.length < 80) {
                    const hasVideo = doc.querySelector('iframe, video, .mediaplugin');
                    text = cleanText(hasVideo || item.title.toLowerCase().includes('видео')
                        ? `Это обучающая видеолекция по теме "${item.title}".`
                        : `Это практический материал по теме "${item.title}".`);
                }

                text = text + "\n\n" + metaString;

                return { ...item, content_text: text.trim(), url: item.href };
            } catch (err) { return null; }
        });

        const parsedChunk = (await Promise.all(promises)).filter(Boolean);

        if (parsedChunk.length > 0) {
            try {
                await fetch("http://127.0.0.1:8000/api/module/bulk-update", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ course_id: getCourseId(), modules: parsedChunk })
                });
            } catch (error) {}
        }
        await new Promise(r => setTimeout(r, 500));
    }
}

async function passiveModuleSync() {
    const id = new URLSearchParams(window.location.search).get('id');
    const mainContent = document.querySelector('[role="main"], #region-main, body');
    const act = document.querySelector(`li.activity#module-${id}`);

    if (id && mainContent) {
        try {
            let text = extractMeaningfulContent(document);
            const visInfo = act ? extractVisibilityInfo(act) : null;

            if (visInfo) {
                let metaContext = [];
                if (visInfo.section_title) metaContext.push("Тема/Раздел курса: " + visInfo.section_title);
                if (visInfo.dates && visInfo.dates.length > 0) metaContext.push("Даты/Сроки: " + visInfo.dates.join("; "));
                if (visInfo.restrictions && visInfo.restrictions.length > 0) metaContext.push("Ограничения доступа: " + visInfo.restrictions.join("; "));
                if (visInfo.completion_rules && visInfo.completion_rules.length > 0) metaContext.push("Условия завершения элемента: " + visInfo.completion_rules.join("; "));
                if (visInfo.resource_details) metaContext.push("Параметры файла: " + visInfo.resource_details);
                if (visInfo.inline_desc) metaContext.push("Описание на странице: " + visInfo.inline_desc);

                if (metaContext.length > 0) {
                    text += "\n\nМЕТАДАННЫЕ ЭЛЕМЕНТА:\n" + metaContext.join("\n");
                }
            }

            await fetch("http://127.0.0.1:8000/api/module/update", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    course_id: getCourseId(),
                    moodle_id: `module-${id}`,
                    module_type: act ? getModuleType(act) : null,
                    content_text: text.trim(),
                    url: window.location.href,
                    visibility: visInfo || { is_hidden: false, has_restrictions: false, raw_text: "" }
                })
            });
        } catch (e) {}
    }
}

async function checkMoodleLogForUpdates() {
    const courseId = getCourseId();
    try {
        const res = await fetch(`/report/log/index.php?id=${courseId}&modaction=cud`, { credentials: 'include' });
        const doc = new DOMParser().parseFromString(await res.text(), "text/html");
        const firstRow = doc.querySelector('#report_log_r0');
        if (!firstRow) return;

        const hash = (firstRow.querySelector('.c0')?.innerText || "") + "_" + (firstRow.querySelector('.c5')?.innerText || "");
        const savedHash = localStorage.getItem(`moodle_bot_version_${courseId}`);

        if (savedHash !== hash) {
            localStorage.setItem(`moodle_bot_version_${courseId}`, hash);
            runSilentSpider();
        }
    } catch (e) {}
}