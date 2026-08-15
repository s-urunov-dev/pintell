/**
 * Translations for the closed sets of English text the API returns.
 *
 * Country names, notice types, procurement methods and project statuses come
 * straight from the World Bank feed, so no amount of UI translation reaches
 * them. They are, however, drawn from small fixed vocabularies — so they are
 * mapped here and rendered through `translateValue()`.
 *
 * Anything not listed falls back to the upstream string unchanged. That is the
 * correct behaviour: free-text upstream content (notice bodies, project names,
 * supplier names) must never be guessed at.
 */
import type { Lang } from './types';

type ValueMap = Record<string, Partial<Record<Lang, string>>>;

/** Lookup key: lowercased, punctuation-insensitive, whitespace-collapsed. */
function normalize(value: string): string {
  return value.trim().toLowerCase().replace(/[\s.,()/-]+/g, ' ').trim();
}

function buildIndex(map: ValueMap): Map<string, Partial<Record<Lang, string>>> {
  return new Map(Object.entries(map).map(([key, value]) => [normalize(key), value]));
}

// -- countries ---------------------------------------------------------------
// The focus region in full, plus the countries that dominate the wider archive.
const COUNTRIES: ValueMap = {
  Afghanistan: { uz: 'Afgʻoniston', ru: 'Афганистан' },
  Albania: { uz: 'Albaniya', ru: 'Албания' },
  Armenia: { uz: 'Armaniston', ru: 'Армения' },
  Azerbaijan: { uz: 'Ozarbayjon', ru: 'Азербайджан' },
  Bangladesh: { uz: 'Bangladesh', ru: 'Бангладеш' },
  Belarus: { uz: 'Belarus', ru: 'Беларусь' },
  Brazil: { uz: 'Braziliya', ru: 'Бразилия' },
  Bulgaria: { uz: 'Bolgariya', ru: 'Болгария' },
  China: { uz: 'Xitoy', ru: 'Китай' },
  Colombia: { uz: 'Kolumbiya', ru: 'Колумбия' },
  'Congo, Democratic Republic of': {
    uz: 'Kongo Demokratik Respublikasi',
    ru: 'Демократическая Республика Конго',
  },
  'Egypt, Arab Republic of': { uz: 'Misr Arab Respublikasi', ru: 'Арабская Республика Египет' },
  Ethiopia: { uz: 'Efiopiya', ru: 'Эфиопия' },
  Georgia: { uz: 'Gruziya', ru: 'Грузия' },
  Ghana: { uz: 'Gana', ru: 'Гана' },
  India: { uz: 'Hindiston', ru: 'Индия' },
  Indonesia: { uz: 'Indoneziya', ru: 'Индонезия' },
  'Iraq': { uz: 'Iroq', ru: 'Ирак' },
  Jordan: { uz: 'Iordaniya', ru: 'Иордания' },
  Kazakhstan: { uz: 'Qozogʻiston', ru: 'Казахстан' },
  Kenya: { uz: 'Keniya', ru: 'Кения' },
  'Kyrgyz Republic': { uz: 'Qirgʻiziston Respublikasi', ru: 'Кыргызская Республика' },
  Kyrgyzstan: { uz: 'Qirgʻiziston', ru: 'Кыргызстан' },
  Moldova: { uz: 'Moldova', ru: 'Молдова' },
  Mongolia: { uz: 'Mugʻuliston', ru: 'Монголия' },
  Morocco: { uz: 'Marokash', ru: 'Марокко' },
  Mozambique: { uz: 'Mozambik', ru: 'Мозамбик' },
  Nepal: { uz: 'Nepal', ru: 'Непал' },
  Nigeria: { uz: 'Nigeriya', ru: 'Нигерия' },
  Pakistan: { uz: 'Pokiston', ru: 'Пакистан' },
  Peru: { uz: 'Peru', ru: 'Перу' },
  Philippines: { uz: 'Filippin', ru: 'Филиппины' },
  Romania: { uz: 'Ruminiya', ru: 'Румыния' },
  'Russian Federation': { uz: 'Rossiya Federatsiyasi', ru: 'Российская Федерация' },
  Rwanda: { uz: 'Ruanda', ru: 'Руанда' },
  Senegal: { uz: 'Senegal', ru: 'Сенегал' },
  'Sri Lanka': { uz: 'Shri-Lanka', ru: 'Шри-Ланка' },
  Tajikistan: { uz: 'Tojikiston', ru: 'Таджикистан' },
  Tanzania: { uz: 'Tanzaniya', ru: 'Танзания' },
  Turkey: { uz: 'Turkiya', ru: 'Турция' },
  Türkiye: { uz: 'Turkiya', ru: 'Турция' },
  Uganda: { uz: 'Uganda', ru: 'Уганда' },
  Ukraine: { uz: 'Ukraina', ru: 'Украина' },
  Uzbekistan: { uz: 'Oʻzbekiston', ru: 'Узбекистан' },
  Vietnam: { uz: 'Vetnam', ru: 'Вьетнам' },
  'Yemen, Republic of': { uz: 'Yaman Respublikasi', ru: 'Республика Йемен' },
  Zambia: { uz: 'Zambiya', ru: 'Замбия' },
  // Regional buckets the feed also uses as a "country".
  'Africa': { uz: 'Afrika', ru: 'Африка' },
  'Central Asia': { uz: 'Markaziy Osiyo', ru: 'Центральная Азия' },
  'Eastern Africa': { uz: 'Sharqiy Afrika', ru: 'Восточная Африка' },
  'Europe and Central Asia': {
    uz: 'Yevropa va Markaziy Osiyo',
    ru: 'Европа и Центральная Азия',
  },
  'South Asia': { uz: 'Janubiy Osiyo', ru: 'Южная Азия' },
  'Western Africa': { uz: 'Gʻarbiy Afrika', ru: 'Западная Африка' },
  World: { uz: 'Dunyo', ru: 'Мир' },
  // Same bucket, French spelling — upstream is inconsistent between awards.
  Monde: { uz: 'Dunyo', ru: 'Мир' },
};

// -- notice types ------------------------------------------------------------
const NOTICE_TYPES: ValueMap = {
  'Request for Expression of Interest': {
    uz: 'Qiziqish bildirish soʻrovi',
    ru: 'Запрос на выражение заинтересованности',
  },
  'Invitation for Bids': { uz: 'Tanlovga taklif', ru: 'Приглашение к участию в торгах' },
  'Invitation for Prequalification': {
    uz: 'Dastlabki malaka bahosiga taklif',
    ru: 'Приглашение к предквалификации',
  },
  'General Procurement Notice': {
    uz: 'Umumiy xarid eʼloni',
    ru: 'Общее закупочное объявление',
  },
  'Contract Award': { uz: 'Shartnoma natijasi', ru: 'Присуждение контракта' },
  'Request for Proposals': { uz: 'Takliflar soʻrovi', ru: 'Запрос предложений' },
  'Request for Bids': { uz: 'Takliflar (bid) soʻrovi', ru: 'Запрос на подачу заявок' },
  'Request for Quotations': { uz: 'Narx soʻrovi', ru: 'Запрос котировок' },
  'Procurement Plan': { uz: 'Xaridlar rejasi', ru: 'План закупок' },
  'Notice of Award': { uz: 'Natija toʻgʻrisidagi eʼlon', ru: 'Уведомление о присуждении' },
};

// -- procurement methods -----------------------------------------------------
const PROCUREMENT_METHODS: ValueMap = {
  'Open International': { uz: 'Ochiq xalqaro', ru: 'Открытый международный' },
  'Open National': { uz: 'Ochiq milliy', ru: 'Открытый национальный' },
  'Limited International': { uz: 'Cheklangan xalqaro', ru: 'Ограниченный международный' },
  'Limited National': { uz: 'Cheklangan milliy', ru: 'Ограниченный национальный' },
  'International Competitive Bidding': {
    uz: 'Xalqaro raqobat asosidagi tanlov',
    ru: 'Международные конкурентные торги',
  },
  'National Competitive Bidding': {
    uz: 'Milliy raqobat asosidagi tanlov',
    ru: 'Национальные конкурентные торги',
  },
  'Direct Selection': { uz: 'Toʻgʻridan-toʻgʻri tanlov', ru: 'Прямой выбор' },
  'Direct Contracting': {
    uz: 'Toʻgʻridan-toʻgʻri shartnoma',
    ru: 'Прямое заключение контракта',
  },
  'Single Source Selection': { uz: 'Yagona manbadan tanlov', ru: 'Выбор из единственного источника' },
  'Quality And Cost-Based Selection': {
    uz: 'Sifat va narx asosidagi tanlov',
    ru: 'Отбор на основе качества и стоимости',
  },
  'Quality Based Selection': {
    uz: 'Sifat asosidagi tanlov',
    ru: 'Отбор на основе качества',
  },
  'Least Cost Selection': { uz: 'Eng past narx boʻyicha tanlov', ru: 'Отбор по наименьшей стоимости' },
  'Fixed Budget Selection': {
    uz: 'Belgilangan byudjet boʻyicha tanlov',
    ru: 'Отбор при фиксированном бюджете',
  },
  'Consultant Qualification Selection': {
    uz: 'Konsultant malakasi boʻyicha tanlov',
    ru: 'Отбор по квалификации консультанта',
  },
  'Individual Consultant Selection': {
    uz: 'Yakka konsultantni tanlash',
    ru: 'Отбор индивидуального консультанта',
  },
  'Request for Expression of Interest': {
    uz: 'Qiziqish bildirish soʻrovi',
    ru: 'Запрос на выражение заинтересованности',
  },
  Shopping: { uz: 'Xarid (shopping)', ru: 'Закупка методом запроса цен' },
  'Framework Agreement': { uz: 'Ramkaviy kelishuv', ru: 'Рамочное соглашение' },
};

// -- statuses ----------------------------------------------------------------
const STATUSES: ValueMap = {
  Active: { uz: 'Faol', ru: 'Активный' },
  Closed: { uz: 'Yopilgan', ru: 'Закрыт' },
  Pipeline: { uz: 'Rejalashtirilgan', ru: 'В подготовке' },
  Dropped: { uz: 'Bekor qilingan', ru: 'Отменён' },
  Published: { uz: 'Eʼlon qilingan', ru: 'Опубликовано' },
  Cancelled: { uz: 'Bekor qilingan', ru: 'Отменено' },
  Canceled: { uz: 'Bekor qilingan', ru: 'Отменено' },
  Draft: { uz: 'Qoralama', ru: 'Черновик' },
  Expired: { uz: 'Muddati oʻtgan', ru: 'Срок истёк' },
  Open: { uz: 'Ochiq', ru: 'Открыт' },
};

// -- document metadata -------------------------------------------------------
const DOC_TYPES: ValueMap = {
  'Procurement Plan': { uz: 'Xaridlar rejasi', ru: 'План закупок' },
  'Environmental and Social Review Summary': {
    uz: 'Ekologik va ijtimoiy baholash xulosasi',
    ru: 'Резюме экологического и социального обзора',
  },
  'Project Information Document': {
    uz: 'Loyiha maʼlumot hujjati',
    ru: 'Информационный документ проекта',
  },
  'Project Appraisal Document': {
    uz: 'Loyihani baholash hujjati',
    ru: 'Документ оценки проекта',
  },
  'Implementation Status and Results Report': {
    uz: 'Ijro holati va natijalar hisoboti',
    ru: 'Отчёт о ходе реализации и результатах',
  },
  'Loan Agreement': { uz: 'Kredit shartnomasi', ru: 'Кредитное соглашение' },
  'Financing Agreement': { uz: 'Moliyalashtirish shartnomasi', ru: 'Соглашение о финансировании' },
  'Auditing Document': { uz: 'Audit hujjati', ru: 'Аудиторский документ' },
  'Procurement Document': { uz: 'Xarid hujjati', ru: 'Закупочный документ' },
  'Environmental and Social Management Plan': {
    uz: 'Ekologik va ijtimoiy boshqaruv rejasi',
    ru: 'План экологического и социального управления',
  },
};

const DOC_LANGUAGES: ValueMap = {
  English: { uz: 'Ingliz tili', ru: 'Английский' },
  French: { uz: 'Fransuz tili', ru: 'Французский' },
  Spanish: { uz: 'Ispan tili', ru: 'Испанский' },
  Russian: { uz: 'Rus tili', ru: 'Русский' },
  Portuguese: { uz: 'Portugal tili', ru: 'Португальский' },
  Arabic: { uz: 'Arab tili', ru: 'Арабский' },
  Chinese: { uz: 'Xitoy tili', ru: 'Китайский' },
  Uzbek: { uz: 'Oʻzbek tili', ru: 'Узбекский' },
};

// -- region group labels and their footnotes ---------------------------------
const REGION_TEXT: ValueMap = {
  'CIS & Afghanistan': { uz: 'MDH va Afgʻoniston', ru: 'СНГ и Афганистан' },
  "Upstream spells Kyrgyzstan as 'Kyrgyz Republic'.": {
    uz: 'Manbada Qirgʻiziston “Kyrgyz Republic” deb yoziladi.',
    ru: 'В источнике Кыргызстан записан как «Kyrgyz Republic».',
  },
  "Upstream spells Russia as 'Russian Federation'.": {
    uz: 'Manbada Rossiya “Russian Federation” deb yoziladi.',
    ru: 'В источнике Россия записана как «Russian Federation».',
  },
  'Winding down participation but still formally on record.': {
    uz: 'Ishtirokini toʻxtatmoqda, biroq rasman roʻyxatda qolmoqda.',
    ru: 'Сворачивает участие, но формально всё ещё в составе.',
  },
  'Not a CIS member; included by product decision.': {
    uz: 'MDH aʼzosi emas; mahsulot qarori bilan kiritilgan.',
    ru: 'Не входит в СНГ; включён по продуктовому решению.',
  },
};

const INDEXES = {
  country: buildIndex(COUNTRIES),
  noticeType: buildIndex(NOTICE_TYPES),
  procurementMethod: buildIndex(PROCUREMENT_METHODS),
  status: buildIndex(STATUSES),
  docType: buildIndex(DOC_TYPES),
  docLanguage: buildIndex(DOC_LANGUAGES),
  region: buildIndex(REGION_TEXT),
} as const;

export type ValueKind = keyof typeof INDEXES;

/**
 * Translate one upstream value, or return it unchanged when it is not part of
 * a known vocabulary.
 */
export function translateValue(
  kind: ValueKind,
  value: string | null | undefined,
  lang: Lang,
): string {
  if (!value) return '';
  if (lang === 'en') return value;
  const entry = INDEXES[kind].get(normalize(value));
  return entry?.[lang] ?? value;
}
