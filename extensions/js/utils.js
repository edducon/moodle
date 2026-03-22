window.MoodleBot = window.MoodleBot || {};
window.MoodleBot.courseDOMsCache = null;

async function getCourseDOMs() {
    if (window.MoodleBot.courseDOMsCache) return window.MoodleBot.courseDOMsCache;

    const doms = [document];

    // НОВОЕ: Ищем ссылки на скрытые секции во ВСЕХ форматах (недели, темы, сетка)
    const linkNodes = document.querySelectorAll(
        'h3.sectionname a[href*="section="], ' +          /* Обычный формат с пагинацией */
        '.thegrid a.grid-section-inner[href*="section="], ' + /* Формат "Сетка" (Grid) */
        '.course-content .section-summary a[href*="section="]' /* Свернутые темы */
    );

    // Собираем уникальные ссылки, чтобы не качать одну страницу дважды
    let links = new Set();
    linkNodes.forEach(a => links.add(a.href));
    const sectionLinks = Array.from(links);

    if (sectionLinks.length > 0) {
        console.log(`[Moodle Bot] Найдена пагинация/сетка. Загружаю ${sectionLinks.length} скрытых разделов...`);
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
            await new Promise(r => setTimeout(r, 200)); // небольшая пауза, чтобы не дудосить сервер
        }
    }

    window.MoodleBot.courseDOMsCache = doms;
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

// === УЛУЧШЕННЫЙ ПАРСЕР ИНФОРМАЦИИ ОБ ЭЛЕМЕНТЕ ===
function extractVisibilityInfo(actElement) {
    if (!actElement) return { is_hidden: false, has_restrictions: false, raw_text: "" };

    let meta = {
        is_hidden: isActivityHidden(actElement),
        has_restrictions: hasRestrictionMarkers(actElement) && !isActivityHidden(actElement),
        section_title: "", // НОВОЕ: Название темы/раздела
        restrictions: [],
        dates: [],
        completion_rules: [],
        resource_details: "",
        inline_desc: "",
        raw_text: cleanText(actElement.innerText || '').slice(0, 1500)
    };

    try {
        // 0. Находим, в какой теме (разделе) находится элемент
        const sectionNode = actElement.closest('li.section.main');
        if (sectionNode) {
            const secTitleNode = sectionNode.querySelector('h3.sectionname');
            if (secTitleNode) {
                meta.section_title = cleanText(secTitleNode.innerText);
            }
        }

        // 1. Даты (ИСПРАВЛЕНО ДУБЛИРОВАНИЕ)
        const datesWrapper = actElement.querySelector('.activity-dates .description-inner');
        if (datesWrapper) {
            // Берем только прямых детей, чтобы не хватать текст родителя
            Array.from(datesWrapper.children).forEach(child => {
                const txt = cleanText(child.innerText);
                if (txt) meta.dates.push(txt);
            });
        } else {
            // Фолбэк на случай другой верстки Moodle
            const altDates = actElement.querySelector('.activity-dates');
            if (altDates) {
                const txt = cleanText(altDates.innerText);
                if (txt) meta.dates.push(txt);
            }
        }

        // 2. Условия доступа и ограничения
        const restrictTree = actElement.querySelectorAll('.availabilityinfo li:not(.showmore)');
        if (restrictTree.length > 0) {
            restrictTree.forEach(li => {
                const txt = cleanText(li.innerText);
                if (txt) meta.restrictions.push(txt);
            });
        } else {
            const restrictBox = actElement.querySelector('.availabilityinfo .description-inner');
            if (restrictBox) {
                let txt = cleanText(restrictBox.innerText);
                txt = txt.replace(/^Недоступно, пока не выполнены условия:\s*/i, '').trim();
                if (txt) meta.restrictions.push(txt);
            }
        }

        // 3. Условия завершения (что нужно сделать студенту)
        const completionEls = actElement.querySelectorAll('.automatic-completion-conditions span.font-weight-normal, [data-region="completion-info"] button');
        completionEls.forEach(el => {
            const txt = cleanText(el.innerText);
            if (txt) meta.completion_rules.push(txt);
        });

        // 4. Детали ресурса (Вес, расширение, дата загрузки файла)
        const resDetails = actElement.querySelector('.resourcelinkdetails');
        if (resDetails) {
            meta.resource_details = cleanText(resDetails.innerText);
        }

        // 5. Текст пояснений и меток (inline-описания)
        const descEls = actElement.querySelectorAll('.activity-altcontent .description-inner, .description .description-inner > .no-overflow');
        let descParts = [];
        descEls.forEach(el => {
            const txt = cleanText(el.innerText);
            // Фильтруем то, что уже собрали в других блоках
            if (txt && !txt.includes('Недоступно') && !txt.includes('Открыто с')) {
                descParts.push(txt);
            }
        });
        meta.inline_desc = Array.from(new Set(descParts)).join(' | ');

    } catch (e) {
        console.error("[Moodle Bot] Ошибка парсинга метаданных элемента:", e);
    }

    return meta;
}

function parseRuDate(dateStr) {
    if (!dateStr) return null;
    if (dateStr instanceof Date) return dateStr;

    const normalized = String(dateStr).trim().toLowerCase().replace(/\s+г\.?$/, '');
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
                return month !== undefined ? new Date(parseInt(m[3]), month, parseInt(m[1]), 23, 59, 0) : null;
            }
        }
    ];

    for (const p of patterns) {
        const match = cleanDate.match(p.regex);
        if (match) return p.handler(match);
    }
    return null;
}

function getCourseMap() {
    let map = [];
    // Берем все скачанные страницы курса (главную + все скрытые темы)
    const doms = window.MoodleBot.courseDOMsCache || [document];

    doms.forEach(doc => {
        // Исключаем .section-summary, чтобы не дублировать блоки
        doc.querySelectorAll('li.section.main:not(.section-summary)').forEach(sec => {
            let secTitleEl = sec.querySelector('h3.sectionname');
            if (!secTitleEl) return;
            let secTitle = secTitleEl.innerText.trim();

            let secDescEl = sec.querySelector('.summarytext');
            let secDesc = secDescEl ? secDescEl.innerText.replace(/\n/g, ' ').trim() : "";

            let items = [];
            sec.querySelectorAll('li.activity').forEach(act => {
                let nameEl = act.querySelector('.instancename');
                if (!nameEl) return;

                let moodleId = act.id;
                let clone = nameEl.cloneNode(true);
                clone.querySelectorAll('.accesshide').forEach(e => e.remove());
                let itemName = clone.innerText.trim();

                let tags = [];
                if (act.classList.contains('hiddenactivity') || act.querySelector('.badge-warning')) {
                    tags.push('[СКРЫТО]');
                }
                let restriction = act.querySelector('.availabilityinfo .description-inner');
                if (restriction) tags.push(`[УСЛОВИЕ ДОСТУПА: ${restriction.innerText.replace(/\n/g, ' ').trim()}]`);

                let completion = act.querySelector('.automatic-completion-conditions');
                if (completion) {
                    let reqs = Array.from(completion.querySelectorAll('span.font-weight-normal')).map(e => e.innerText.trim());
                    if (reqs.length > 0) tags.push(`[ДЛЯ ЗАВЕРШЕНИЯ НУЖНО: ${reqs.join(', ')}]`);
                }

                let tagStr = tags.length > 0 ? ` ${tags.join(' ')}` : '';
                items.push(`ID: ${moodleId} | ${itemName}${tagStr}`);
            });

            if (items.length > 0) {
                let descStr = secDesc ? `\n  Описание/Правила: ${secDesc}` : '';
                map.push(`Раздел [${secTitle}]:${descStr}\n  ` + items.join('\n  '));
            }
        });
    });
    return map.join('\n\n').substring(0, 3000);
}

async function getCourseTeachers() {
    const courseId = getCourseId();
    if (!courseId) return "Преподаватели неизвестны";

    const cacheKey = `moodle_teachers_${courseId}`;
    if (sessionStorage.getItem(cacheKey)) return sessionStorage.getItem(cacheKey);

    try {
        const response = await fetch(`https://online.mospolytech.ru/user/index.php?id=${courseId}&perpage=5000`);
        const html = await response.text();
        const doc = new DOMParser().parseFromString(html, "text/html");

        let teachers = [];
        doc.querySelectorAll('table#participants tbody tr').forEach(row => {
            const roleCell = row.querySelector('td.c7');
            if (roleCell && roleCell.innerText.includes('Преподаватель')) {
                const nameCell = row.querySelector('th.c1');
                const emailCell = row.querySelector('td.c3');
                if (nameCell) {
                    let name = nameCell.innerText.trim();
                    let email = emailCell ? emailCell.innerText.trim() : '';
                    teachers.push(`${name} (${email})`);
                }
            }
        });
        const result = teachers.length > 0 ? "Преподаватели: " + teachers.join(', ') : "Преподаватели не найдены";
        sessionStorage.setItem(cacheKey, result);
        return result;
    } catch (e) {
        return "Не удалось загрузить список преподавателей";
    }
}

function getCurrentPageContext() {
    let context = [];
    let intro = document.querySelector('.activity-description #intro') || document.querySelector('.box.generalbox');
    if (intro) context.push("ОПИСАНИЕ ИЛИ УСЛОВИЯ: " + intro.innerText.replace(/\n/g, ' ').trim());

    let dates = document.querySelector('.activity-dates');
    if (dates) context.push("СРОКИ: " + dates.innerText.replace(/\n/g, ' ').trim());

    let quizInfo = document.querySelectorAll('.quizinfo p');
    if (quizInfo.length > 0) {
        let rules = Array.from(quizInfo).map(p => p.innerText.trim());
        context.push("ПРАВИЛА ТЕСТА: " + rules.join(' | '));
    }

    let conditions = document.querySelector('.automatic-completion-conditions');
    if (conditions) {
        let reqs = Array.from(conditions.querySelectorAll('.badge')).map(e => e.innerText.replace(/\n/g, ' ').trim());
        if (reqs.length > 0) context.push("СТАТУС ВЫПОЛНЕНИЯ ТРЕБОВАНИЙ: " + reqs.join(' | '));
    }
    return context.length > 0 ? context.join('\n') : "";
}

function getStudentGrades() {
    let gradesTable = document.querySelector('table.user-grade');
    if (!gradesTable) return "";

    let results = [];
    gradesTable.querySelectorAll('tr.item').forEach(tr => {
        let nameEl = tr.querySelector('.column-itemname .gradeitemheader');
        let gradeEl = tr.querySelector('.column-grade');
        if (nameEl && gradeEl) {
            let name = nameEl.innerText.replace(/\n/g, ' ').trim();
            let grade = gradeEl.innerText.trim();
            if (grade === '-') grade = 'Нет оценки';
            results.push(`- ${name}: ${grade}`);
        }
    });
    return results.length > 0 ? "ВЫПИСКА ОЦЕНОК СТУДЕНТА:\n" + results.join('\n') : "";
}

function getAssignmentStatus() {
    let statusTable = document.querySelector('.submissionstatustable table');
    if (!statusTable) return "";

    let rows = [];
    statusTable.querySelectorAll('tr').forEach(tr => {
        let th = tr.querySelector('th');
        let td = tr.querySelector('td');
        if (th && td) {
            rows.push(`${th.innerText.trim()}: ${td.innerText.replace(/\n/g, ' ').trim()}`);
        }
    });
    return rows.length > 0 ? "СТАТУС СДАЧИ ТЕКУЩЕГО ЗАДАНИЯ:\n" + rows.join('\n') : "";
}

function cleanText(text) {
    return (text || '').replace(/\s+/g, ' ').trim();
}

function extractModuleTitle(act) {
    if (!act) return "Без названия";
    const selectors = ['.instancename', '.aalink .instancename', '.activityname', '.name', 'a.aalink'];

    for (const selector of selectors) {
        const el = act.querySelector(selector);
        if (el) {
            let text = cleanText(el.innerText || el.textContent || '');
            text = text.replace(/\s*Файл\s*$/i, '').replace(/\s*URL\s*$/i, '').replace(/\s*Папка\s*$/i, '')
                       .replace(/\s*Страница\s*$/i, '').replace(/\s*Книга\s*$/i, '').replace(/\s*Отметить как выполненный\s*$/i, '');
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
    const mainNode = doc.querySelector('[role="main"]') || doc.querySelector('#region-main') || doc.querySelector('.resourcecontent') || doc.querySelector('.box.generalbox') || doc.querySelector('.book_content') || doc.body;
    if (!mainNode) return '';

    const clone = mainNode.cloneNode(true);
    const selectorsToRemove = ['#nav-drawer', '[data-region="drawer"]', '[data-region="message-drawer"]', '.popover-region', '#page-footer', 'footer', 'nav', '.navbar', '.block', '.activity-navigation', '.sr-only', '.hidden', 'script', 'style'];

    selectorsToRemove.forEach(selector => {
        clone.querySelectorAll(selector).forEach(el => el.remove());
    });

    return cleanText(clone.innerText || clone.textContent || '');
}

function shouldIndexModuleType(type) {
    // Если тип пустой, игнорируем
    if (!type) return false;

    // Если нужно игнорировать какие-то конкретные системные модули,
    // можно добавить их сюда. Но по умолчанию теперь разрешаем ВСЕ типы.
    const ignoredTypes = ['scorm', 'feedback'];

    return !ignoredTypes.includes(type);
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