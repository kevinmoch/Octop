/**
 * authored-slides 投放：收**结构化演示模型**，渲染成 reveal 能放映的 HTML/CSS，
 * 并把原样模型挂回根节点供另存 PPTX 使用。
 *
 * 两件事在这里被一起解决：
 * 1. 屏幕上那份和另存的 PPTX 以前是两条独立通路，必然漂开；现在两边读同一个 JSON。
 * 2. 版面空。字号与版式由本文件独占——模型给不出「小字」，也给不出「一页三行字」：
 *    每种 layout 的槽位数、每个槽位的最低内容量都在这里当场校验。
 */

const CHART_TYPES = ['bar', 'line', 'area', 'pie', 'scatter', 'stacked-bar', 'dual-axis'];
const ITEM_TYPES = ['chart', 'table', 'image', 'metrics', 'keyValue', 'bullets', 'paragraph'];
const DATA_ITEMS = ['chart', 'table', 'image', 'metrics', 'keyValue'];
/** 图片引用的定位前缀；字节由引擎在投放前按引用填入，技能不搬运字节 */
const IMAGE_REF_PREFIXES = ['artifact:', 'upload:', 'remote:'];
const LAYOUTS = ['cover', 'section', 'single', 'split', 'grid', 'closing'];
/** 需要正文槽位的版式；封面与过渡页不吃这套密度规则 */
const CONTENT_LAYOUTS = ['single', 'split', 'grid', 'closing'];

/** 每种版式的槽位数下限 / 上限。改这里就等于改「一页最少要有多少东西」 */
const SLOTS = {
  cover: [0, 1],
  section: [0, 1],
  single: [1, 1],
  split: [2, 2],
  grid: [3, 4],
  closing: [1, 2]
};

const PALETTES = {
  dark: {
    background: '#0f1b2e',
    surface: '#17253c',
    text: '#f2f7ff',
    muted: '#a8bcd9',
    accent: '#4da3ff',
    border: '#2a3d5c'
  },
  light: {
    background: '#ffffff',
    surface: '#f2f6fb',
    text: '#16202e',
    muted: '#5c6b7f',
    accent: '#1a6fd4',
    border: '#dbe3ee'
  }
};

// ---------------------------------------------------------------- 排版表

/**
 * 排版表。屏幕字号、页面几何、篇幅预算三者共用这一份——
 * 分两份写必然漂开：版面被撑破的根源就是校验看不见 CSS 用了多大的字。
 * 单位一律是 1600×900 舞台上的 px。
 */
const STAGE = { width: 1600, height: 900 };
const PAGE = { padX: 72, padY: 64, gap: 28 };
/** rule 是页眉底下那条强调线连同它的下内边距，整体占的高度 */
const HEAD = { title: 44, titleLh: 1.25, sub: 24, subLh: 1.4, gap: 10, rule: 22, border: 4 };
const TAKEAWAY = { size: 26, lh: 1.5, padY: 20, padX: 26, border: 8 };
const SLOT = { padX: 32, padY: 28, gap: 16, border: 1 };
const COVER = { title: 64, titleLh: 1.2, sub: 30, meta: 22 };
const BODY_LH = 1.55;
const BULLET_GAP = 10;

/** 正文字号按槽位数分档：槽越窄，字越小 */
const TYPE = {
  1: { body: 26, slotTitle: 28, table: 22, kv: 24, caption: 20, metric: 60, metricLabel: 24 },
  2: { body: 23, slotTitle: 25, table: 20, kv: 22, caption: 19, metric: 52, metricLabel: 22 },
  3: { body: 20, slotTitle: 22, table: 18, kv: 19, caption: 18, metric: 44, metricLabel: 20 },
  4: { body: 19, slotTitle: 21, table: 17, kv: 18, caption: 17, metric: 40, metricLabel: 19 }
};

/** 页眉与结论带的行数上限。多出来的每一行都是从正文区里挖走的 */
const LINE_CAPS = { title: 1, subtitle: 1, takeaway: 2, heroTitle: 3 };

/** 与 CSS 的 grid-template-columns 同一套 */
const GRID_COLS = { 1: 1, 2: 2, 3: 3, 4: 2 };

const STAGE_INNER_W = STAGE.width - PAGE.padX * 2;
const STAGE_INNER_H = STAGE.height - PAGE.padY * 2;

/** 视觉宽度：CJK / 全角算一格，其余算半格。按字符数算会把英文稿误判成两倍长 */
const FULL_WIDTH =
  /[\u1100-\u115F\u2E80-\uA4CF\uA960-\uA97F\uAC00-\uD7FF\uF900-\uFAFF\uFE10-\uFE6F\uFF00-\uFF60\uFFE0-\uFFE6]/;
const widthOf = (text) => [...String(text)].reduce((sum, ch) => sum + (FULL_WIDTH.test(ch) ? 1 : 0.5), 0);

/**
 * 「一个汉字一格」是理想模型，真实字距总要更宽一点：标点、比例数字、字体回退都会偏，
 * 恰好卡在整行边界的句子会比算出来多折一行。留 3% 余量，宁可早一行折。
 */
const SAFETY = 0.97;

/** 一格约等于一个字号宽，所以行数 = 视觉宽度 × 字号 ÷ 可用宽度。估算偏保守，方向与兜底层一致 */
const linesOf = (text, size, boxWidth) => Math.max(1, Math.ceil((widthOf(text) * size) / (boxWidth * SAFETY)));

const typeFor = (slots) => TYPE[Math.min(Math.max(slots, 1), 4)];

function headHeight(slide) {
  const title = isText(slide.title) ? slide.title : '';
  let height = linesOf(title, HEAD.title, STAGE_INNER_W) * HEAD.title * HEAD.titleLh + HEAD.rule;
  if (isText(slide.subtitle)) {
    height += HEAD.gap + linesOf(slide.subtitle, HEAD.sub, STAGE_INNER_W) * HEAD.sub * HEAD.subLh;
  }
  return height;
}

function takeawayHeight(slide) {
  if (!isText(slide.takeaway)) return 0;
  const width = STAGE_INNER_W - TAKEAWAY.border - TAKEAWAY.padX * 2;
  return linesOf(slide.takeaway, TAKEAWAY.size, width) * TAKEAWAY.size * TAKEAWAY.lh + TAKEAWAY.padY * 2;
}

/** 一个槽位的内容区尺寸（已扣掉槽位自己的边框与内边距）。纯算术，与浏览器实测逐像素对过 */
function slotBox(slide) {
  const count = slide.body.length;
  const cols = GRID_COLS[count] ?? 1;
  const rows = Math.ceil(count / cols);
  const bodyH = STAGE_INNER_H - headHeight(slide) - takeawayHeight(slide) - PAGE.gap * 2;
  const chrome = SLOT.border * 2;
  return {
    width: (STAGE_INNER_W - PAGE.gap * (cols - 1)) / cols - SLOT.padX * 2 - chrome,
    height: (bodyH - PAGE.gap * (rows - 1)) / rows - SLOT.padY * 2 - chrome
  };
}

/**
 * 这一块需要多高。图表 / 图片 / 指标卡返回 0 不是偷懒：
 * 它们在 CSS 里是 flex: 1 1 auto，高度由剩余空间决定，本来就不会溢出，
 * 算进去只会造成假阳性。
 */
function itemHeight(item, box, type) {
  const head = isText(item.title) ? type.slotTitle * 1.3 + SLOT.gap : 0;
  switch (item.type) {
    case 'bullets': {
      const width = box.width - type.body * 1.2; // ul 的 padding-left 是 1.2em
      const lines = item.items.reduce((sum, one) => sum + linesOf(one, type.body, width), 0);
      return head + lines * type.body * BODY_LH + (item.items.length - 1) * BULLET_GAP;
    }
    case 'paragraph':
      return head + linesOf(item.text, type.body, box.width) * type.body * BODY_LH;
    case 'keyValue':
      return head + item.items.length * (type.kv * 1.4 + 12);
    case 'table':
      return head + (item.rows.length + 1) * (type.table * 1.4 + 25);
    default:
      return 0;
  }
}

// ---------------------------------------------------------------- 校验

function validate(deck, dataSource) {
  const issues = [];
  if (typeof dataSource !== 'string' || dataSource.trim() === '') {
    issues.push('dataSource is empty; state where the numbers came from');
  }
  if (typeof deck !== 'object' || deck === null || Array.isArray(deck)) {
    issues.push('deck must be an object shaped like references/authoring.md');
    return issues;
  }
  if (!isText(deck.title)) issues.push('deck.title is required');
  if (deck.theme !== undefined && deck.theme !== 'dark' && deck.theme !== 'light') {
    issues.push('deck.theme must be "dark" or "light"');
  }
  if (!Array.isArray(deck.slides) || deck.slides.length < 3) {
    issues.push(
      `deck.slides needs at least 3 slides (a cover, at least one content slide and a closing); found ${Array.isArray(deck.slides) ? deck.slides.length : 0}`
    );
    return issues;
  }

  let dataSlides = 0;
  deck.slides.forEach((slide, index) => {
    const at = `deck.slides[${index}]`;
    const found = slideIssues(slide, at);
    for (const issue of found) issues.push(issue);
    if (found.length === 0 && Array.isArray(slide.body) && slide.body.some((item) => DATA_ITEMS.includes(item.type))) {
      dataSlides += 1;
    }
  });
  if (issues.length === 0 && dataSlides === 0) {
    issues.push(
      'not one slide carries a chart, table, image, metrics or keyValue; a deck made only of bullet points is exactly the sparse deck this skill exists to avoid — put the numbers you gathered on the slides'
    );
  }
  return issues;
}

function slideIssues(slide, at) {
  if (typeof slide !== 'object' || slide === null || Array.isArray(slide)) return [`${at} must be an object`];
  const issues = [];
  if (!LAYOUTS.includes(slide.layout)) {
    return [`${at}.layout "${String(slide.layout)}" is unknown; allowed: ${LAYOUTS.join(', ')}`];
  }
  if (!isText(slide.title)) issues.push(`${at}.title is required on every slide`);

  const body = Array.isArray(slide.body) ? slide.body : [];
  if (!Array.isArray(slide.body)) issues.push(`${at}.body must be an array (use [] on a cover or section divider)`);
  const [min, max] = SLOTS[slide.layout];
  if (body.length < min || body.length > max) {
    issues.push(
      `${at}.layout "${slide.layout}" holds ${min === max ? min : `${min}-${max}`} body item${max > 1 ? 's' : ''} but got ${body.length}; pick the layout that matches how much you have`
    );
  }
  body.forEach((item, index) => {
    for (const issue of itemIssues(item, `${at}.body[${index}]`)) issues.push(issue);
  });

  if (CONTENT_LAYOUTS.includes(slide.layout)) {
    if (!isText(slide.takeaway)) {
      issues.push(
        `${at}.takeaway is required on a "${slide.layout}" slide; one sentence saying what the audience should conclude — it also fills the bottom band the layout reserves`
      );
    } else if (slide.takeaway.trim().length < 8) {
      issues.push(`${at}.takeaway is too short to be a conclusion; write a full sentence`);
    }
  }
  if (slide.layout === 'single' && body.length === 1 && body[0] && body[0].type === 'paragraph') {
    issues.push(
      `${at} is a "single" slide holding nothing but one paragraph, which leaves the stage almost empty; either add the chart or table it describes and use "split", or move the prose into takeaway plus a "bullets" item`
    );
  }
  // 形状先对，篇幅再谈：块的字段还不成立时，估高度只会算出一串没意义的数字
  if (issues.length === 0) {
    for (const issue of budgetIssues(slide, at)) issues.push(issue);
  }
  return issues;
}

/**
 * 篇幅预算：按这一页**真实的**标题 / 副标题 / 结论长度算出正文可用高度，
 * 再按分档字号估算每个槽位要多高。超了当场挡回——裁剪是兜底，不该在合法输入上发生。
 */
function budgetIssues(slide, at) {
  const issues = [];
  const hero = slide.layout === 'cover' || slide.layout === 'section';

  const titleSize = hero ? COVER.title : HEAD.title;
  const titleCap = hero ? LINE_CAPS.heroTitle : LINE_CAPS.title;
  const titleLines = linesOf(slide.title, titleSize, STAGE_INNER_W);
  if (titleLines > titleCap) {
    issues.push(
      `${at}.title wraps to ${titleLines} lines; keep it to ${titleCap} (about ${cellBudget(STAGE_INNER_W, titleSize, titleCap)} full-width characters) — ` +
        (hero
          ? 'a title this long pushes the subtitle and the metadata line off the bottom of the slide'
          : 'every extra line is taken out of the body area below')
    );
  }

  if (!hero && isText(slide.subtitle)) {
    const lines = linesOf(slide.subtitle, HEAD.sub, STAGE_INNER_W);
    if (lines > LINE_CAPS.subtitle) {
      issues.push(
        `${at}.subtitle wraps to ${lines} lines; keep it to ${LINE_CAPS.subtitle} (about ${cellBudget(STAGE_INNER_W, HEAD.sub, LINE_CAPS.subtitle)} full-width characters) — it sits above the body and every extra line is taken out of it`
      );
    }
  }

  if (isText(slide.takeaway)) {
    const width = STAGE_INNER_W - TAKEAWAY.border - TAKEAWAY.padX * 2;
    const lines = linesOf(slide.takeaway, TAKEAWAY.size, width);
    if (lines > LINE_CAPS.takeaway) {
      issues.push(
        `${at}.takeaway wraps to ${lines} lines; keep it to ${LINE_CAPS.takeaway} (about ${cellBudget(width, TAKEAWAY.size, LINE_CAPS.takeaway)} full-width characters) — the bottom band grows downward into the body area above it`
      );
    }
  }

  if (hero || slide.body.length === 0) return issues;

  const count = slide.body.length;
  const type = typeFor(count);
  const box = slotBox(slide);
  const lineH = type.body * BODY_LH;
  const fits = Math.max(1, Math.floor(box.height / lineH));
  slide.body.forEach((item, index) => {
    const need = itemHeight(item, box, type);
    if (need <= box.height) return;
    // 三条出路按代价从低到高。不提「调小字号」：模型没有那个入口，说了只会让它反复重试
    issues.push(
      `${at}.body[${index}] needs about ${Math.ceil(need / lineH)} lines but a ${count}-slot layout fits about ${fits}; ` +
        `shorten it to roughly ${cellBudget(box.width, type.body, fits)} full-width characters, drop a body item, ` +
        'or split this slide in two. The renderer will not shrink the type any further.'
    );
  });
  return issues;
}

/** 一块区域放得下多少「格」（一个汉字一格，一个西文字母半格）。与 linesOf 同用一份余量，免得建议的字数照样被退 */
const cellBudget = (width, size, lines) => Math.floor(((width * SAFETY) / size) * lines);

function itemIssues(item, at) {
  if (typeof item !== 'object' || item === null || Array.isArray(item)) return [`${at} must be an object`];
  if (!ITEM_TYPES.includes(item.type)) {
    return [`${at}.type "${String(item.type)}" is unknown; allowed: ${ITEM_TYPES.join(', ')}`];
  }
  const issues = [];
  switch (item.type) {
    case 'chart':
      for (const issue of chartIssues(item, at)) issues.push(issue);
      break;
    case 'table':
      for (const issue of tableIssues(item, at)) issues.push(issue);
      break;
    case 'image':
      for (const issue of imageIssues(item, at)) issues.push(issue);
      break;
    case 'metrics':
      if (!Array.isArray(item.items) || item.items.length < 2) {
        issues.push(`${at}.items needs at least 2 metrics; a lone number belongs in the slide title or takeaway`);
      } else if (item.items.length > 4) {
        issues.push(
          `${at}.items has ${item.items.length} metrics; keep it to 4 so each one stays readable on a projector`
        );
      } else {
        item.items.forEach((one, i) => {
          if (!one || !isText(one.label)) issues.push(`${at}.items[${i}].label is required`);
          if (!one || one.value === undefined || one.value === null) issues.push(`${at}.items[${i}].value is required`);
        });
      }
      break;
    case 'keyValue':
      if (!Array.isArray(item.items) || item.items.length < 3) {
        issues.push(`${at}.items needs at least 3 rows, otherwise the slot looks empty`);
      }
      break;
    case 'bullets':
      if (!Array.isArray(item.items) || item.items.length < 3) {
        issues.push(
          `${at}.items has ${Array.isArray(item.items) ? item.items.length : 0} bullets; a slot of bullets needs at least 3 or the slide reads as empty`
        );
      } else if (item.items.length > 6) {
        issues.push(
          `${at}.items has ${item.items.length} bullets; split them across two slides rather than shrinking the text`
        );
      } else {
        item.items.forEach((one, i) => {
          if (!isText(one)) issues.push(`${at}.items[${i}] is empty`);
        });
      }
      break;
    case 'paragraph':
      if (!isText(item.text)) issues.push(`${at}.text is required`);
      else if (item.text.trim().length < 24) {
        issues.push(`${at}.text is a fragment, not a paragraph; say the whole thought or make it a bullets item`);
      }
      break;
    default:
      break;
  }
  return issues;
}

function tableIssues(block, at) {
  const issues = [];
  if (!Array.isArray(block.columns) || block.columns.length === 0) {
    issues.push(`${at}.columns must be a non-empty array of column headers`);
    return issues;
  }
  if (!Array.isArray(block.rows) || block.rows.length === 0) {
    issues.push(`${at}.rows must be a non-empty array`);
    return issues;
  }
  if (block.rows.length > 8) {
    issues.push(`${at}.rows has ${block.rows.length} rows; a slide holds about 8 — summarise or split the table`);
  }
  block.rows.forEach((row, i) => {
    if (!Array.isArray(row)) {
      issues.push(`${at}.rows[${i}] must be an array of cells`);
      return;
    }
    if (row.length !== block.columns.length) {
      issues.push(`${at}.rows[${i}] has ${row.length} cells but there are ${block.columns.length} columns`);
    }
  });
  return issues;
}

function chartIssues(block, at) {
  const issues = [];
  if (!CHART_TYPES.includes(block.chartType)) {
    issues.push(`${at}.chartType "${String(block.chartType)}" is unknown; allowed: ${CHART_TYPES.join(', ')}`);
  }
  if (!Array.isArray(block.labels) || block.labels.length === 0) {
    issues.push(`${at}.labels must be a non-empty array`);
  }
  if (!Array.isArray(block.series) || block.series.length === 0) {
    issues.push(`${at}.series must be a non-empty array`);
    return issues;
  }
  block.series.forEach((series, i) => {
    if (!series || !isText(series.name)) issues.push(`${at}.series[${i}].name is required`);
    if (!series || !Array.isArray(series.values)) {
      issues.push(`${at}.series[${i}].values must be an array of numbers`);
      return;
    }
    if (Array.isArray(block.labels) && series.values.length !== block.labels.length) {
      issues.push(
        `${at}.series[${i}].values has ${series.values.length} numbers but there are ${block.labels.length} labels`
      );
    }
    series.values.forEach((value, j) => {
      if (typeof value !== 'number' || !Number.isFinite(value)) {
        issues.push(`${at}.series[${i}].values[${j}] must be a number, not a string`);
      }
    });
  });
  return issues;
}

/**
 * 与公文侧同一份形状：只收引用与替代文本。
 * 宽高与 MIME 是事实，由宿主侧解析时实测，不收声明。
 */
function imageIssues(item, at) {
  const issues = [];
  if (!isText(item.ref)) {
    issues.push(`${at}.ref is required; point at an image artifact or an uploaded file`);
  } else if (!IMAGE_REF_PREFIXES.some((prefix) => item.ref.startsWith(prefix))) {
    issues.push(`${at}.ref "${item.ref}" needs one of these prefixes: ${IMAGE_REF_PREFIXES.join(', ')}`);
  }
  if (!isText(item.alt)) {
    issues.push(`${at}.alt is required; it is the only thing left if the image cannot be loaded`);
  }
  if (item.width !== undefined || item.height !== undefined || item.mimeType !== undefined) {
    issues.push(`${at} must not declare width, height or mimeType; the host measures them from the bytes`);
  }
  return issues;
}

const isText = (value) => typeof value === 'string' && value.trim() !== '';

/**
 * 正文里的一句话只收标量。放富文本对象（或对象数组）过去，
 * `String(值)` 会在投放页面上印出 `[object Object]`——版式完好只是读不成句，比报错难查。
 */
const isScalar = (value) => typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean';

// ---------------------------------------------------------------- 渲染

const escapeText = (value) =>
  String(isScalar(value) ? value : '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');

const escapeAttr = (value) => JSON.stringify(value).replace(/&/g, '&amp;').replace(/'/g, '&#39;').replace(/</g, '&lt;');

/** 文本进双引号属性：比 escapeText 多挡两种引号，否则 alt 里一个引号就能拆开标签 */
const escapeAttrText = (value) => escapeText(value).replace(/"/g, '&quot;').replace(/'/g, '&#39;');

function inline(text) {
  const parts = String(isScalar(text) ? text : '').split('**');
  return parts
    .map((part, index) => (index % 2 === 1 ? `<strong>${escapeText(part)}</strong>` : escapeText(part)))
    .join('');
}

function normalise(deck) {
  // 默认浅色：多数场合是屏幕共享 / 文档分发，不是暗场投屏
  const theme = deck.theme === 'dark' ? 'dark' : 'light';
  return {
    kind: 'deck',
    title: String(deck.title).trim(),
    theme,
    palette: PALETTES[theme],
    slides: deck.slides.map((slide) => {
      const out = { layout: slide.layout, body: Array.isArray(slide.body) ? slide.body : [] };
      if (isText(slide.title)) out.title = slide.title.trim();
      if (isText(slide.subtitle)) out.subtitle = slide.subtitle.trim();
      if (Array.isArray(slide.meta) && slide.meta.length > 0) {
        out.meta = slide.meta
          .filter((item) => item && isText(item.label))
          .map((item) => ({ label: String(item.label), value: String(item.value === undefined ? '' : item.value) }));
      }
      if (isText(slide.takeaway)) out.takeaway = slide.takeaway.trim();
      return out;
    })
  };
}

function renderHtml(model) {
  const sections = model.slides.map(renderSlide).join('');
  const attrs = [
    `class="deck deck--${model.theme}"`,
    'data-viewer-mode="slides"',
    'data-viewer-chart-font="lg"',
    'data-webskill-doc="deck"',
    `data-webskill-doc-model='${escapeAttr(model)}'`
  ].join(' ');
  return `<div ${attrs}><div class="slides">${sections}</div></div>`;
}

function renderSlide(slide) {
  const parts = [];
  if (slide.layout === 'cover' || slide.layout === 'section') {
    parts.push('<div class="deck__hero">');
    parts.push(`<h1 class="deck__herotitle">${escapeText(slide.title)}</h1>`);
    parts.push('<span class="deck__accent"></span>');
    if (slide.subtitle) parts.push(`<p class="deck__herosub">${escapeText(slide.subtitle)}</p>`);
    parts.push('</div>');
    if (slide.meta && slide.meta.length > 0) {
      const items = slide.meta
        .map((item) => `<span class="deck__metaitem">${escapeText(item.label)}：${escapeText(item.value)}</span>`)
        .join('');
      parts.push(`<p class="deck__meta">${items}</p>`);
    }
    for (const item of slide.body) parts.push(renderItem(item));
    return `<section class="deck__slide" data-layout="${slide.layout}">${parts.join('')}</section>`;
  }

  parts.push(`<header class="deck__head"><h2 class="deck__title">${escapeText(slide.title)}</h2>`);
  if (slide.subtitle) parts.push(`<p class="deck__sub">${escapeText(slide.subtitle)}</p>`);
  parts.push('</header>');
  const slots = slide.body.map((item) => `<div class="deck__slot">${renderItem(item)}</div>`).join('');
  parts.push(`<div class="deck__body" data-slots="${slide.body.length}">${slots}</div>`);
  if (slide.takeaway) parts.push(`<footer class="deck__takeaway">${inline(slide.takeaway)}</footer>`);
  return `<section class="deck__slide" data-layout="${slide.layout}">${parts.join('')}</section>`;
}

function renderItem(item) {
  const heading = isText(item.title) ? `<h3 class="deck__slottitle">${escapeText(item.title)}</h3>` : '';
  switch (item.type) {
    case 'chart': {
      const props = { type: item.chartType, labels: item.labels.map(String), series: item.series };
      if (isText(item.title)) props.title = item.title;
      const caption = isText(item.caption) ? `<p class="deck__caption">${escapeText(item.caption)}</p>` : '';
      return `<div class="deck__chart" data-webskill-component="Chart" data-webskill-props='${escapeAttr(props)}'></div>${caption}`;
    }
    case 'table': {
      const props = { columns: item.columns.map(String), rows: item.rows };
      if (Array.isArray(item.columnWidths)) props.columnWidths = item.columnWidths;
      const caption = isText(item.caption) ? `<p class="deck__caption">${escapeText(item.caption)}</p>` : '';
      return `${heading}<div class="deck__table" data-webskill-component="Table" data-webskill-props='${escapeAttr(props)}'></div>${caption}`;
    }
    case 'image': {
      // 故意不写 src：字节由引擎在投放前按 data-webskill-image 填入
      const caption = isText(item.caption) ? `<p class="deck__caption">${escapeText(item.caption)}</p>` : '';
      const img = `<img class="deck__image" data-webskill-image="${escapeAttrText(item.ref)}" alt="${escapeAttrText(item.alt)}" />`;
      return `${img}${caption}`;
    }
    case 'metrics': {
      const cells = item.items
        .map((one) => {
          const props = { label: String(one.label), value: one.value };
          if (isText(one.change)) props.change = one.change;
          if (one.trend) props.trend = one.trend;
          return `<div class="deck__metric" data-webskill-component="Metric" data-webskill-props='${escapeAttr(props)}'></div>`;
        })
        .join('');
      return `${heading}<div class="deck__metrics">${cells}</div>`;
    }
    case 'keyValue': {
      const props = {
        items: item.items.map((one) => ({
          label: String(one && one.label !== undefined ? one.label : ''),
          value: String(one && one.value !== undefined ? one.value : '')
        }))
      };
      return `${heading}<div class="deck__kv" data-webskill-component="KeyValue" data-webskill-props='${escapeAttr(props)}'></div>`;
    }
    case 'bullets': {
      const items = item.items.map((one) => `<li>${inline(one)}</li>`).join('');
      return `${heading}<ul class="deck__bullets">${items}</ul>`;
    }
    case 'paragraph':
      return `${heading}<p class="deck__para">${inline(item.text)}</p>`;
    default:
      return '';
  }
}

/**
 * 样式表是**固定**的，模型不写 CSS。字号全部由上面那张排版表生成，
 * 校验读的也是它——「校验看不见 CSS 用了多大的字」这件事在这里被消掉。
 *
 * 所有选择器都带 `.reveal` 前缀：技能样式先于放映引擎的样式注入，同分即输，
 * 不带前缀的 color 压不过引擎默认字色。也一律用后代选择器 `.slides section`，
 * 因为打印时每个 section 会被包进 `.pdf-page`，子选择器当场失配。
 */
const FIXED_CSS = [
  '.deck.reveal { background: var(--deck-bg); color: var(--deck-text); }',
  ".deck.reveal { font-family: 'PingFang SC', 'Microsoft YaHei', system-ui, sans-serif; }",
  '.deck--dark { --deck-bg: #0f1b2e; --deck-surface: #17253c; --deck-text: #f2f7ff; --deck-muted: #a8bcd9; --deck-accent: #4da3ff; --deck-border: #2a3d5c; }',
  '.deck--light { --deck-bg: #ffffff; --deck-surface: #f2f6fb; --deck-text: #16202e; --deck-muted: #5c6b7f; --deck-accent: #1a6fd4; --deck-border: #dbe3ee; }',
  // overflow 是几何兜底：估算再准也可能被字体回退带偏，丢半行字好过把结论句切成两半
  `.deck.reveal .slides section { flex-direction: column; gap: ${PAGE.gap}px; padding: ${PAGE.padY}px ${PAGE.padX}px; text-align: left; background: var(--deck-bg); color: var(--deck-text); height: 100%; box-sizing: border-box; overflow: hidden; }`,
  '.deck.reveal .slides section[data-layout="cover"], .deck.reveal .slides section[data-layout="section"] { justify-content: center; }',
  '.deck.reveal .slides section[data-layout="cover"] { background: linear-gradient(135deg, var(--deck-bg) 0%, var(--deck-surface) 100%); }',
  '.deck.reveal .deck__hero { display: flex; flex-direction: column; gap: 20px; }',
  `.deck.reveal .deck__herotitle { margin: 0; font-size: ${COVER.title}px; line-height: ${COVER.titleLh}; font-weight: 700; color: var(--deck-text); }`,
  '.deck.reveal .deck__accent { display: block; width: 160px; height: 8px; background: var(--deck-accent); }',
  `.deck.reveal .deck__herosub { margin: 0; font-size: ${COVER.sub}px; color: var(--deck-muted); }`,
  `.deck.reveal .deck__meta { margin: 40px 0 0; font-size: ${COVER.meta}px; color: var(--deck-muted); }`,
  '.deck.reveal .deck__metaitem + .deck__metaitem { margin-left: 36px; }',
  `.deck.reveal .deck__head { display: flex; flex-direction: column; gap: ${HEAD.gap}px; border-bottom: ${HEAD.border}px solid var(--deck-accent); padding-bottom: ${HEAD.rule - HEAD.border}px; }`,
  `.deck.reveal .deck__title { margin: 0; font-size: ${HEAD.title}px; line-height: ${HEAD.titleLh}; font-weight: 700; color: var(--deck-text); }`,
  `.deck.reveal .deck__sub { margin: 0; font-size: ${HEAD.sub}px; line-height: ${HEAD.subLh}; color: var(--deck-muted); }`,
  `.deck.reveal .deck__body { flex: 1 1 auto; display: grid; gap: ${PAGE.gap}px; min-height: 0; grid-auto-rows: minmax(0, 1fr); }`,
  `.deck.reveal .deck__body[data-slots="2"] { grid-template-columns: repeat(${GRID_COLS[2]}, 1fr); }`,
  `.deck.reveal .deck__body[data-slots="3"] { grid-template-columns: repeat(${GRID_COLS[3]}, 1fr); }`,
  `.deck.reveal .deck__body[data-slots="4"] { grid-template-columns: repeat(${GRID_COLS[4]}, 1fr); }`,
  `.deck.reveal .deck__slot { display: flex; flex-direction: column; gap: ${SLOT.gap}px; min-height: 0; min-width: 0; background: var(--deck-surface); border: 1px solid var(--deck-border); border-radius: 14px; padding: ${SLOT.padY}px ${SLOT.padX}px; overflow: hidden; }`,
  '.deck.reveal .deck__slottitle { margin: 0; font-weight: 700; color: var(--deck-accent); }',
  '.deck.reveal .deck__chart { flex: 1 1 auto; min-height: 0; }',
  '.deck.reveal .deck__image { flex: 1 1 auto; min-height: 0; width: 100%; object-fit: contain; }',
  '.deck.reveal .deck__caption { margin: 0; color: var(--deck-muted); }',
  `.deck.reveal .deck__para { margin: 0; line-height: ${BODY_LH}; color: var(--deck-text); }`,
  `.deck.reveal .deck__bullets { margin: 0; padding-left: 1.2em; line-height: ${BODY_LH}; color: var(--deck-text); }`,
  `.deck.reveal .deck__bullets li { margin-bottom: ${BULLET_GAP}px; }`,
  '.deck.reveal .deck__bullets li::marker { color: var(--deck-accent); }',
  '.deck.reveal .deck__metrics { flex: 1 1 auto; display: flex; gap: 20px; align-items: stretch; }',
  '.deck.reveal .deck__metric { flex: 1 1 0; display: flex; flex-direction: column; justify-content: center; gap: 8px; }',
  '.deck.reveal .deck__metric [data-webskill-metric="label"] { color: var(--deck-muted); }',
  '.deck.reveal .deck__metric [data-webskill-metric="value"] { font-weight: 700; color: var(--deck-accent); line-height: 1.1; }',
  '.deck.reveal .deck__metric [data-webskill-metric="change"] { color: var(--deck-muted); }',
  '.deck.reveal .deck__table { flex: 1 1 auto; min-height: 0; overflow: hidden; }',
  '.deck.reveal .deck__table table { width: 100%; border-collapse: collapse; }',
  '.deck.reveal .deck__table th, .deck.reveal .deck__table td { border-bottom: 1px solid var(--deck-border); padding: 12px 14px; text-align: left; color: var(--deck-text); }',
  '.deck.reveal .deck__table th { color: var(--deck-muted); font-weight: 700; }',
  '.deck.reveal .deck__kv dl { display: grid; grid-template-columns: auto 1fr; gap: 12px 24px; margin: 0; }',
  '.deck.reveal .deck__kv dt { color: var(--deck-muted); }',
  '.deck.reveal .deck__kv dd { margin: 0; color: var(--deck-text); font-weight: 700; }',
  `.deck.reveal .deck__takeaway { flex: 0 0 auto; background: var(--deck-surface); border-left: ${TAKEAWAY.border}px solid var(--deck-accent); padding: ${TAKEAWAY.padY}px ${TAKEAWAY.padX}px; font-size: ${TAKEAWAY.size}px; line-height: ${TAKEAWAY.lh}; color: var(--deck-text); }`,
  `.deck.reveal [data-webskill-fallback] { color: var(--deck-muted); font-size: ${TYPE[2].body}px; }`
];

/** 槽位内的字号逐档展开。data-slots 是 renderSlide 本来就在写的属性，不需要新增标记 */
const typeCss = (at, type) => [
  `${at} .deck__bullets { font-size: ${type.body}px; }`,
  `${at} .deck__para { font-size: ${type.body}px; }`,
  `${at} .deck__slottitle { font-size: ${type.slotTitle}px; }`,
  `${at} .deck__table table { font-size: ${type.table}px; }`,
  `${at} .deck__kv dl { font-size: ${type.kv}px; }`,
  `${at} .deck__caption { font-size: ${type.caption}px; }`,
  `${at} .deck__metric [data-webskill-metric="value"] { font-size: ${type.metric}px; }`,
  `${at} .deck__metric [data-webskill-metric="label"] { font-size: ${type.metricLabel}px; }`,
  `${at} .deck__metric [data-webskill-metric="change"] { font-size: ${type.metricLabel}px; }`
];

// 封面与过渡页不走 deck__body 栅格，它们的块直接挂在 section 上——先给一档最宽松的底
const SLOT_TYPE_CSS = [
  ...typeCss('.deck.reveal .slides section', TYPE[1]),
  ...Object.entries(TYPE).flatMap(([slots, type]) => typeCss(`.deck.reveal .deck__body[data-slots="${slots}"]`, type))
];

const CSS = [...FIXED_CSS, ...SLOT_TYPE_CSS].join('\n');

// ---------------------------------------------------------------- 工具

export const inputSchema = {
  type: 'object',
  properties: {
    deck: {
      type: 'object',
      description:
        'The deck as structured data, not markup. Shape: { title, theme?: "dark" | "light", slides: [{ layout, title, subtitle?, meta?, body: [...], takeaway? }] }. theme defaults to "light"; omit it unless the deck is meant for a darkened room, and only then pass "dark". Layouts: cover, section, single, split, grid, closing — each one accepts a fixed number of body items and the renderer owns every font size, which shrinks as the layout holds more slots. Length is validated too: a slot that does not fit is rejected with how many lines it needs versus how many fit, so read the message and shorten, drop a body item or split the slide — retrying the same deck will fail the same way. Body item types: chart, table, metrics, keyValue, bullets, paragraph. See references/authoring.md for every field, for how full each slide has to be and for how much each layout holds.',
      properties: {
        title: { type: 'string' },
        theme: { type: 'string', enum: ['dark', 'light'], description: 'Optional. Defaults to "light". Pass "dark" only for a darkened-room projection.' },
        slides: { type: 'array', items: { type: 'object' } }
      },
      required: ['title', 'slides']
    },
    dataSource: {
      type: 'string',
      description:
        'Where the numbers actually came from, shown to the user in the document window. Name the real origins you read, e.g. "page: 付款管理 / 合同台账" or "tools: list_bugs, get_dora_metrics". Never claim a source you did not read.'
    }
  },
  required: ['deck', 'dataSource']
};

export async function run(input, context) {
  const issues = validate(input && input.deck, input && input.dataSource);
  if (issues.length > 0) {
    return [
      {
        type: 'text',
        text: `PUBLISH_REJECTED: ${issues.join(' | ')}. Nothing was published — fix the deck object and call authored-slides__publish again.`
      }
    ];
  }

  const model = normalise(input.deck);
  await context.writeArtifact('deck.html', renderHtml(model), {
    mimeType: 'text/html',
    metadata: { resultCard: false }
  });
  await context.writeArtifact('deck.css', CSS, { mimeType: 'text/css', metadata: { resultCard: false } });

  // 宿主没接文档面时按钮根本不会渲染（SDK 分册 13 FR-13.3），发了就是「模型以为有、用户看不到」。
  if (context.documentSurface !== true) {
    return [
      {
        type: 'text',
        text: 'The deck artifacts were written, but this host has no document surface, so there is no way to present them here. Tell the user the deck is in the managed skill storage and summarize the narrative inline instead.'
      }
    ];
  }

  return [
    // 不写 label：缺省文案跟随宿主界面语言，写死就把技能钉死在一种语言上。
    {
      type: 'json',
      data: {
        $surface: [
          {
            type: 'open',
            id: 'authored-slides-open',
            node: {
              component: 'OpenDocument',
              props: { artifact: 'deck.html', style: 'deck.css', dataSource: input.dataSource }
            }
          }
        ]
      }
    },
    {
      type: 'text',
      text: 'The deck is published. The open button in this result is already rendered and is the ONLY UI entry point — do NOT call render_ui or emit any other button, link or chart for it, and do not mention downloads or attachments. Reply with one or two sentences on the narrative arc, tell the user they can navigate with the arrow keys, print a PDF and export a PPTX that matches what is on screen, and invite them to say in plain language which slide they want changed; do not restate the numbers on the slides, and keep the deck object you just sent so you can revise it.'
    }
  ];
}
