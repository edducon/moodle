window.MoodleBot = window.MoodleBot || {};
window.MoodleBot.courseDOMsCache = null;

async function getCourseDOMs() {
    if (window.MoodleBot.courseDOMsCache) return window.MoodleBot.courseDOMsCache;

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
    if (dateStr instanceof Date) return dateStr; // <--- ЗАЩИТА: если это уже дата, просто возвращаем её!

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
    return ['page', 'resource', 'assign', 'book', 'quiz', 'url', 'label', 'lesson', 'folder', 'forum', 'chat', 'checklist'].includes(type);
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