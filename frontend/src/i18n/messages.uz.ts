import type { Catalogue } from './messages.en';

/**
 * O'zbekcha katalog — mahsulotning standart tili.
 *
 * O'zbek tilida son bilan kelgan ot ko'plik qo'shimchasini olmaydi
 * ("3 kun qoldi"), shuning uchun `_one` va `_other` shakllari bir xil.
 */
const uz: Catalogue = {
  // -- hujjat / brend --------------------------------------------------------
  'meta.title': 'Pintell — Global xarid e’lonlari',
  'meta.description':
    'Jahon banki xarid e’lonlarini butun dunyo bo‘ylab yagona qulay interfeysda ko‘rib chiqing.',
  'brand.tagline': 'Global xarid e’lonlari',

  // -- sahifa karkasi --------------------------------------------------------
  'layout.homeAria': 'Pintell bosh sahifasi',
  'layout.allTenders': 'Barcha tenderlar',
  'layout.switchToLight': 'Yorug‘ mavzuga o‘tish',
  'layout.switchToDark': 'Qorong‘i mavzuga o‘tish',
  'layout.languageAria': 'Interfeys tili',
  'layout.companies': 'Kompaniyalar',
  'layout.awards': 'Yakunlangan tenderlar',
  'layout.experts': 'Ekspertlar',

  // -- expert directory ------------------------------------------------------
  'experts.title': 'Ekspertlar katalogi',
  'experts.lead':
    'Konsalting tenderlari nomlaydigan rollarda ishlaydigan mutaxassislar — Team Leader, Resettlement Specialist, Auditor. Tender talab qilgan rolni to‘ldira olmasangiz, o‘sha tenderga umuman qatnasha olmaysiz — shuning uchun katalog tenderlar yonida turadi.',
  'experts.filterAria': 'Ekspertlarni filtrlash',
  'experts.searchPlaceholder': 'Ism…',
  'experts.role': 'Rol',
  'experts.allRoles': 'Barcha rollar',
  'experts.family': 'Yo‘nalish',
  'experts.allFamilies': 'Barcha yo‘nalishlar',
  'experts.sortBy': 'Saralash',
  'experts.sortName': 'Ism (A–Z)',
  'experts.sortNameDesc': 'Ism (Z–A)',
  'experts.sortUpdated': 'Yaqinda yangilangan',
  'experts.count_one': '{count} ekspert',
  'experts.count_other': '{count} ekspert',
  'experts.colName': 'Ism',
  'experts.colRoles': 'Rollar',
  'experts.colProfile': 'Profil',
  'experts.profileLink': 'LinkedIn',
  'experts.noProfile': 'Havola kiritilmagan',
  'experts.emptyTitle': 'Bu filtrlar bo‘yicha hech kim yo‘q',
  'experts.emptyBody':
    'Katalog qo‘lda to‘ldiriladi va hali to‘lib bormoqda. Kengroq yo‘nalishni tanlang yoki qidiruvni tozalang.',
  'experts.clear': 'Filtrlarni tozalash',

  // -- the team a tender names -----------------------------------------------
  'noticeExperts.title': 'Bu tender talab qiladigan ekspertlar',
  'noticeExperts.lead':
    'Tender matnining o‘zi taklif qiluvchi jamoaga kiritishni so‘ragan lavozimlar. Har birida shuni so‘ragan jumla keltirilgan.',
  'noticeExperts.none':
    'Biz o‘qigan matnda bu tender hech qanday ekspert lavozimini nomlamagan.',
  'noticeExperts.mandatory': 'Majburiy',
  'noticeExperts.desirable': 'Ma’qul',
  'noticeExperts.needed': '{count} ta kerak',
  'noticeExperts.unfiled': 'Katalogimizda bunday rol yo‘q',
  'noticeExperts.candidates': 'Katalogimizdan',
  'noticeExperts.noCandidates': 'Bu rol bo‘yicha hozircha hech kim yo‘q.',
  'noticeExperts.candidatesNote':
    'Bular tenderdan emas, o‘z katalogimizdan taklif. Ularning hech biri bu tenderga nisbatan baholanmagan.',
  'noticeExperts.seeAll': 'Katalogda barchasini ko‘rish',
  'noticeExperts.withheld_one': '{count} ta lavozim ko‘rsatilmadi: iqtiboti manbadan topilmadi.',
  'noticeExperts.withheld_other': '{count} ta lavozim ko‘rsatilmadi: iqtiboti manbadan topilmadi.',

  // -- yakunlangan shartnomalar (kim yutgan va yana kim qatnashgan) -----------
  'awards.title': 'Yakuni chiqqan shartnomalar',
  'awards.lead':
    'Kim yutgan, yana kim baholangan va kim rad etilgan — har bir e’lon o‘zi qanday e’lon qilgan bo‘lsa. Har bir yozuv asl manbaga bog‘langan.',
  'awards.filterAria': 'Yakunlangan tenderlarni filtrlash',
  'awards.searchPlaceholder': 'Kompaniya yoki shartnoma…',
  'awards.role': 'Quyidagilar nomlangan shartnomalar',
  'awards.roleAll': 'Barcha natijalar',
  'awards.roleEvaluated': 'Boshqa ishtirokchilar baholangan',
  'awards.roleRejected': 'Ishtirokchilar rad etilgan',
  'awards.count': '{count} ta shartnoma',
  'awards.awardee': 'G‘olib',
  'awards.evaluated': 'Yana baholangan',
  'awards.rejected': 'Rad etilgan',
  'awards.openNotice': 'E’lonni ochish',
  'awards.openUpstream': 'Manba sahifasi',
  'awards.emptyTitle': 'Mos shartnoma topilmadi',
  'awards.emptyBody': 'Kengroq yo‘nalish tanlang yoki davlat filtrini olib tashlang.',
  'similar.title': 'Shu yo‘nalishda allaqachon berilgan shartnomalar',
  'similar.lead':
    'Xuddi shu yo‘nalishdagi yakunlangan shartnomalar, eng yangisidan boshlab. Kontekst uchun — bu tender haqida hech narsa demaydi.',
  'similar.note':
    'Faqat yo‘nalish bo‘yicha tanlangan va shartnoma sanasi bo‘yicha tartiblangan. Baholanmagan va bashorat emas.',
  'similar.noWinner': 'G‘olib nomlanmagan',
  'similar.alsoBid': 'Yana qatnashganlar:',
  'similar.openAward': 'Shartnomani ochish',
  'similar.openUpstream': 'Manba sahifasi',
  'similar.role.evaluated': '(baholangan)',
  'similar.role.rejected': '(rad etilgan)',

  // -- kompaniyalar (raqobatchilar ro'yxati) ---------------------------------
  'companies.title': 'Shartnoma yutgan kompaniyalar',
  'companies.lead':
    'Jahon banki shartnomalarini yutgan yetkazib beruvchilar — chop etilgan natija e’lonlaridan yig‘ilgan. Yutuqlar soni bo‘yicha tartiblangan, lekin bu reyting emas: bu yerdagi kompaniyalarning ko‘pchiligi bir marta uchraydi, shuning uchun raqamlar ballga aylantirilmay, o‘z holicha ko‘rsatiladi.',
  'companies.filterAria': 'Kompaniyalarni filtrlash',
  'companies.searchPlaceholder': 'Kompaniya nomi…',
  'companies.sortBy': 'Tartiblash',
  'companies.sortWins': 'Eng ko‘p yutgan',
  'companies.sortLatest': 'Eng so‘nggi yutuq',
  'companies.sortValue': 'Eng katta summa (USD)',
  'companies.sortName': 'Nomi (A–Z)',
  'companies.count_one': '{count} ta kompaniya',
  'companies.count_other': '{count} ta kompaniya',
  'companies.colName': 'Kompaniya',
  'companies.colCountry': 'Davlat',
  'companies.colWins': 'Yutuqlar',
  'companies.colValue': 'Summa (USD)',
  'companies.colLatest': 'So‘nggi yutuq',
  'companies.ofWins': '{total} tadan {count} tasi bo‘yicha',
  'companies.emptyTitle': 'Bu filtrlarga mos kompaniya topilmadi',
  'companies.emptyBody': 'Boshqa yo‘nalishni tanlang yoki qidiruv maydonini tozalang.',
  'companies.back': 'Kompaniyalarga qaytish',
  'companies.firstAward': 'Birinchi yutuq',
  'companies.byCategory': 'Yo‘nalishlar bo‘yicha',
  'companies.byCountry': 'Davlatlar bo‘yicha',
  'companies.awards': 'Yutilgan shartnomalar',
  'layout.dataSource': 'Ma’lumot manbayi:',
  'layout.dataSourceName': 'Jahon banki guruhining xarid e’lonlari',
  'layout.disclaimer':
    'Pintell Jahon bankining ochiq ma’lumotlarini aks ettiradi va Jahon banki guruhi bilan aloqador emas hamda u tomonidan tasdiqlanmagan.',

  // -- tenderlar ro'yxati ----------------------------------------------------
  'list.titleFocus': 'Mintaqangizdagi ochiq tenderlar',
  'list.titleArchive': 'Global tender e’lonlari',
  'list.leadFocus':
    'MDH davlatlari va Afg‘oniston bo‘yicha faol imkoniyatlar yo‘nalishlar bo‘yicha turkumlangan — siz faqat o‘zingiz qatnashadigan ishlarni kuzatasiz. Har 30 daqiqada yangilanadi.',
  'list.leadArchive':
    'Jahon bankining to‘liq xaridlar arxivi mahalliy nusxada saqlanadi va har 30 daqiqada avtomatik yangilanadi.',
  'list.stat.openOpportunities': 'Ochiq imkoniyatlar',
  'list.stat.closingToday': 'Bugun yopiladi',
  'list.stat.countries': 'Davlatlar',
  'list.stat.categorised': 'Turkumlangan',
  'list.stat.latestNotice': 'Oxirgi e’lon',
  'list.stat.noticesMirrored': 'Saqlangan e’lonlar',
  'list.stat.currentlyOpen': 'Hozir ochiq',
  'list.stat.countriesRegions': 'Davlat va mintaqalar',
  'list.stat.archiveBackTo': 'Arxiv boshlanishi',
  'list.empty.title': 'Bu filtrlarga mos tender topilmadi',
  'list.empty.focus':
    'Hozircha fokus mintaqasida ochiq e’lon yo‘q. Filtrlarni kengaytiring yoki butun arxiv bo‘yicha izlash uchun fokus rejimini o‘chiring.',
  'list.empty.archive':
    'Davlat yoki usul filtrini kengaytirib ko‘ring yoki qidiruv maydonini tozalang.',

  // -- arxivni yuklash paneli ------------------------------------------------
  'archive.importing_one':
    'Tarixiy arxiv yuklanmoqda — {percent}% ({done}/{total} bo‘lim)',
  'archive.importing_other':
    'Tarixiy arxiv yuklanmoqda — {percent}% ({done}/{total} bo‘lim)',
  'archive.stored_one': '{stored} ta e’lon saqlandi',
  'archive.stored_other': '{stored} ta e’lon saqlandi',
  'archive.storedOf_one': '{total} tadan {stored} ta e’lon saqlandi',
  'archive.storedOf_other': '{total} tadan {stored} ta e’lon saqlandi',

  // -- filtrlar --------------------------------------------------------------
  'filter.aria': 'Tenderlarni filtrlash',
  'filter.focusLabel': '{group} · faqat ochiq imkoniyatlar',
  'filter.focusFallbackGroup': 'Fokus mintaqasi',
  'filter.focusHint':
    'Topshirish muddati o‘tmagan qiziqish bildirish so‘rovlari va tanlovga takliflar.',
  'filter.countriesAria': 'Fokus mintaqasidagi davlatlar',
  'filter.search': 'Qidiruv',
  'filter.searchPlaceholder': 'Tavsif, loyiha, ma’lumotnoma raqami…',
  'filter.category': 'Yo‘nalish',
  'filter.allCategories': 'Barcha yo‘nalishlar',
  'filter.subcategory': 'Konsalting yo‘nalishi',
  'filter.allSubcategories': 'Barcha konsalting ishlari',
  'subcategory.engineering': 'Muhandislik, loyihalash va nazorat',
  'subcategory.audit': 'Audit va moliyaviy boshqaruv',
  'subcategory.environment_social': 'Ekologik va ijtimoiy',
  'subcategory.training': 'O‘qitish va salohiyat oshirish',
  'subcategory.research': 'Tadqiqot va baholash',
  'subcategory.it_advisory': 'IT va raqamli konsalting',
  'subcategory.legal_procurement': 'Huquq va xarid maslahati',
  'subcategory.management': 'Loyiha boshqaruviga ko‘mak',
  'subcategory.other': 'Boshqa konsalting',
  'filter.audience': 'Ishtirokchi turi',
  'filter.allAudiences': 'Firmalar va yakka mutaxassislar',
  'audience.firm': 'Konsalting firmalari',
  'audience.individual': 'Yakka konsultantlar',

  // Kartadagi chip uchun qisqa shakllar. `other` yo‘q — u hech qachon
  // ko‘rsatilmaydi, chunki hech nima demaydi.
  'subcategoryShort.engineering': 'Muhandislik',
  'subcategoryShort.audit': 'Audit',
  'subcategoryShort.environment_social': 'Ekologiya',
  'subcategoryShort.training': 'O‘qitish',
  'subcategoryShort.research': 'Tadqiqot',
  'subcategoryShort.it_advisory': 'IT konsalting',
  'subcategoryShort.legal_procurement': 'Huquq/xarid',
  'subcategoryShort.management': 'Boshqaruv',
  'filter.country': 'Davlat',
  'filter.allCountries': 'Barcha davlatlar',
  'filter.method': 'Xarid usuli',
  'filter.allMethods': 'Barcha usullar',
  'filter.noticeType': 'E’lon turi',
  'filter.allTypes': 'Barcha turlar',
  'filter.deadlineStatusAria': 'Muddat holati',
  'filter.statusAll': 'Barchasi',
  'filter.statusOpen': 'Ochiq',
  'filter.statusClosed': 'Yopilgan',
  'filter.resultCount_one': '{count} ta e’lon',
  'filter.resultCount_other': '{count} ta e’lon',
  'filter.clear': 'Filtrlarni tozalash',

  // -- tender kartochkasi ----------------------------------------------------
  'card.noticeFallback': 'E’lon',
  'card.country': 'Davlat',
  'card.method': 'Usul',
  'card.published': 'E’lon qilingan',
  'card.deadline': 'Muddat',
  'card.viewDetails': 'Batafsil',

  // -- tender tafsilotlari ---------------------------------------------------
  'detail.back': 'Tenderlarga qaytish',
  'detail.noticeText': 'E’lon matni',
  'detail.contact': 'Aloqa',
  'detail.keyFacts': 'Asosiy ma’lumotlar',
  'detail.fact.country': 'Davlat / mintaqa',
  'detail.fact.deadline': 'Muddat',
  'detail.fact.deadlineTime': 'Muddat vaqti',
  'detail.fact.published': 'E’lon qilingan',
  'detail.fact.method': 'Xarid usuli',
  'detail.fact.methodCode': 'Usul kodi',
  'detail.fact.reference': 'Ma’lumotnoma',
  'detail.fact.projectId': 'Loyiha ID',
  'detail.fact.noticeId': 'E’lon ID',
  'detail.fact.language': 'Til',
  'detail.openUpstream': 'worldbank.org saytida ochish →',
  'detail.mirrored': '{when} holatiga ko‘ra saqlangan',
  'detail.winChance': 'Yutish ehtimoli',
  'detail.winChanceSoon': 'Tez orada',
  'detail.showFullText': 'E’lonni to‘liq o‘qish',
  'detail.hideFullText': 'Yig‘ish',
  'detail.budget': 'Loyiha byudjeti',
  'detail.agency': 'Ijrochi tashkilot',
  'detail.overview': 'Qisqacha',
  'detail.bankContact': 'Jahon banki jamoasi',
  'detail.bankContactHint': 'Tender emas, loyiha bo‘yicha mas’ul',
  'detail.subConfidence': 'Yo‘nalish ishonchliligi: {percent}',
  'detail.moreContact': 'Qo‘shimcha aloqa ma’lumotlari',

  // -- e'lon matnidan topilgan texnik topshiriq ------------------------------
  'tor.title': 'Texnik topshiriq (TOR)',
  'tor.open': 'TOR ni ochish →',
  'tor.fromNotice': 'Havola e’lon matnida berilgan.',
  'tor.request': 'TOR ni e-pochta orqali so‘rash',
  'tor.requestHint': 'Havola berilmagan; e’londa {email} manziliga yozish so‘ralgan.',
  'tor.mailSubject': 'Texnik topshiriq (TOR) so‘rovi — {title}',
  'tor.mentionedOnly': 'E’londa TOR tilga olingan, ammo na havola, na manzil berilgan. Quyidagi to‘liq matnni o‘qing.',
  'tor.biddingDocument': 'Tender hujjati',
  'tor.otherLink': 'E’londagi havola',

  // -- muddat sanog'i --------------------------------------------------------
  'countdown.label': 'Topshirish muddati',
  'countdown.days': 'kun',
  'countdown.hours': 'soat',
  'countdown.minutes': 'daq',
  'countdown.seconds': 'son',
  'countdown.closed': 'Yopilgan',
  'countdown.approximate':
    'Vaqt {zone} bo‘yicha; bu davlatda bir necha vaqt mintaqasi bor — manbadan tasdiqlang.',

  'contact.organization': 'Tashkilot',
  'contact.name': 'Mas’ul shaxs',
  'contact.email': 'E-pochta',
  'contact.phone': 'Telefon',
  'contact.address': 'Manzil',
  'contact.country': 'Davlat',
  'contact.website': 'Veb-sayt',

  // -- uchta kontakt qatlami --------------------------------------------------
  'contacts.title': 'Kim bilan bog‘lanish kerak',
  'contacts.tier.notice': 'E’londa ko‘rsatilgan kontakt',
  'contacts.tier.noticeHint': 'E’lonning o‘z kontakt maydonlarida nomi ko‘rsatilgan.',
  'contacts.tier.body': 'E’lon matnida ham keltirilgan',
  'contacts.tier.bodyHint':
    'E’lon oxiridagi manzil blokidan o‘qildi — takliflar ko‘pincha aynan shu yerga yuboriladi.',
  'contacts.tier.bank': 'Jahon banki jamoasi',
  'contacts.tier.bankHint':
    'Bankda loyiha uchun mas’ul, ushbu tender uchun emas. Avval yuqoridagi buyurtmachiga murojaat qiling.',
  'contacts.purpose.submission': 'Takliflar shu yerga',
  'contacts.purpose.enquiry': 'Savollar uchun',
  'contacts.purpose.tor': 'ToR so‘rash uchun',
  'contacts.alsoAt': 'Yana quyidagi manzilda',
  'contacts.samePerson': 'Yuqoridagi shaxsning o‘zi, boshqa manzil',
  'contacts.unnamed': 'Manzil ism ko‘rsatilmasdan e’lon qilingan',
  'contacts.emailUnconfirmed': 'Tasdiqlanmagan — Bank xodimlari manzil qolipiga mos',
  'contacts.emailConfirmed': 'Jahon banki sahifasida e’lon qilingan',
  'contacts.emailFromEsrs': 'Loyihaning ESRS hujjatida e’lon qilingan',
  'contacts.noEmail': 'Manzil e’lon qilinmagan',
  'contacts.parsedNote':
    'E’lon matnidan avtomatik o‘qildi — quyidagi to‘liq matn bilan solishtirib tekshiring.',
  'contacts.profileLink': 'Profil ↗',
  'contacts.publicationLink': 'Nashrlar ↗',
  'contacts.otherLink': 'Havola ↗',

  // -- team lead sahifasi -----------------------------------------------------
  'lead.published': 'E’lon qilingan profil',
  'lead.title': 'Lavozimi',
  'lead.unit': 'Bo‘limi',
  'lead.office': 'Ish joyi',
  'lead.projects': 'Loyihalar',
  'lead.notices': 'Tenderlar',
  'lead.openNotices': 'Hozir ochiq',
  'lead.projectsTitle': 'U yuritayotgan loyihalar',
  'lead.noticesTitle': 'Shu loyihalardan chiqqan tenderlar',
  'lead.noProjects': 'Bu shaxsni team lead sifatida ko‘rsatgan loyiha topilmadi.',
  'lead.noNotices': 'Bu loyihalar uchun hali tenderlar ko‘chirilmagan.',
  'lead.open': 'Ochiq',
  'lead.checked': '{when} da qidirilgan',
  'lead.notCheckedYet': 'Bu shaxs hali qidirilmagan — faqat ismi ma’lum. To‘ldirish uchun team lead enrichment’ni ishga tushiring.',
  'lead.scopeTitle': 'Bu sahifada nima bor',
  'lead.scopeNote': 'Faqat Jahon banki e’lon qilgan kasbiy ma’lumot: lavozim, bo‘lim, ish joyi, ish pochtasi va ochiq kasbiy sahifalar. Shaxsiy ijtimoiy tarmoq akkauntlari, messenjer raqamlari va suratlar yig‘ilmaydi.',
  'lead.viewProfile': 'Profilni ochish →',
  'lead.bankPage': 'Jahon bankidagi sahifasi ↗',


  // -- shartnoma g'olibi -----------------------------------------------------
  'award.title': 'Shartnoma natijasi',
  'award.awardedTo': 'G‘olib',
  'award.notPublished': 'E’lon qilinmagan',
  'award.companyWebsite': 'Kompaniya sayti ↗',
  'award.contractPrice': 'Imzolangan shartnoma narxi',
  'award.evaluatedPrice': 'Baholangan taklif narxi',
  'award.bidPriceOpening': 'Ochilishdagi taklif narxi',
  'award.awardDate': 'Natija e’lon qilingan',
  'award.duration': 'Muddati',
  'award.otherBidders': 'Baholangan boshqa ishtirokchilar',
  'award.unnamedBidder': 'Nomi ko‘rsatilmagan ishtirokchi',
  'award.websiteNote':
    'Kompaniya sayti veb-qidiruv orqali avtomatik topilgan — unga tayanishdan oldin tekshiring.',

  // -- loyiha hujjatlari -----------------------------------------------------
  'project.title': '{id} loyihasi',
  'project.loadError': 'Loyiha hujjatlarini yuklab bo‘lmadi: {error}',
  'project.notMirrored':
    'Bu loyiha hali saqlanmagan — uning hujjatlari navbatdagi boyitish siklidan so‘ng shu yerda paydo bo‘ladi.',
  'project.untitled': 'Nomsiz loyiha',
  'project.page': 'Loyiha sahifasi ↗',
  'project.status': 'Holati',
  'project.totalCost': 'Loyihaning umumiy qiymati',
  'project.commitment': 'Majburiyat summasi',
  'project.implementingAgency': 'Ijrochi tashkilot',
  'project.instrument': 'Moliyalash vositasi',
  'project.closingDate': 'Yopilish sanasi',
  'project.documents': 'Loyiha hujjatlari',
  'project.downloadPdf': 'PDF faylni yuklab olish',
  'project.olderRevisions_one': '{count} ta oldingi tahrir',
  'project.olderRevisions_other': '{count} ta oldingi tahrir',
  'project.undatedRevision': 'Sanasi yo‘q versiya',
  'project.otherDocuments_one': 'Yana {count} ta hujjat (ma’muriy)',
  'project.otherDocuments_other': 'Yana {count} ta hujjat (ma’muriy)',
  'project.noDocuments': 'Bu loyiha bo‘yicha hujjatlar e’lon qilinmagan.',
  'project.untitledDocument': 'Nomsiz hujjat',

  // -- umumiy holatlar -------------------------------------------------------
  'state.errorTitle': 'Nimadir noto‘g‘ri ketdi',
  'state.tryAgain': 'Qayta urinish',
  'state.loadingTenders': 'Tenderlar yuklanmoqda',
  'state.loadingTender': 'Tender yuklanmoqda',
  'notice.noText': 'Bu tender bo‘yicha e’lon matni chop etilmagan.',

  'notFound.title': 'Sahifa topilmadi',
  'notFound.description': 'Bu manzil Pintell’dagi hech narsaga mos kelmadi.',

  'pagination.aria': 'Sahifalash',
  'pagination.previous': '← Oldingi',
  'pagination.next': 'Keyingi →',
  'pagination.page': '{page}-sahifa',

  // -- muddatlar -------------------------------------------------------------
  'deadline.none': 'Muddat e’lon qilinmagan',
  'deadline.left_one': '{count} kun qoldi',
  'deadline.left_other': '{count} kun qoldi',
  'deadline.hours_one': '{count} soat qoldi',
  'deadline.hours_other': '{count} soat qoldi',
  'deadline.lastHour': 'Bir soatdan kam qoldi',
  'deadline.today': 'Bugun yopiladi',
  'deadline.yesterday': 'Kecha yopildi',
  'deadline.ago_one': '{count} kun oldin yopildi',
  'deadline.ago_other': '{count} kun oldin yopildi',

  // -- tender yo'nalishlari --------------------------------------------------
  'category.construction': 'Qurilish va ishlar',
  'category.consulting': 'Konsalting',
  'category.supply': 'Tovar yetkazib berish',
  'category.services': 'Xizmatlar',
  'category.it': 'IT va raqamli xizmatlar',
  'category.other': 'Boshqa',
  'category.unknown': 'Turkumlanmagan',

  'categoryShort.construction': 'Qurilish',
  'categoryShort.consulting': 'Konsalting',
  'categoryShort.supply': 'Yetkazib berish',
  'categoryShort.services': 'Xizmatlar',
  'categoryShort.it': 'IT',
  'categoryShort.other': 'Boshqa',
  'categoryShort.unknown': 'Turkumlanmagan',

  'categorySource.agent': 'Agent o‘qib turkumlagan',
  'categorySource.ai': 'E’lon hujjati asosida sun’iy intellekt turkumlagan',
  'categorySource.rules': 'E’lon matnidagi so‘zlar bo‘yicha turkumlangan',
  'categorySource.manual': 'Operator tomonidan qo‘lda belgilangan',
  'categorySource.none': 'Hali turkumlanmagan',

  // -- kompaniya profili -----------------------------------------------------
  // Bu bo'limdagi barcha matnlar uchun qoida: to'ldirilmagan maydon — bu
  // *javob berilmagan* savol, *muvaffaqiyatsizlik* emas. Bo'sh qoldirilgan
  // katak kompaniyaga qarshi hisoblanishini anglatuvchi bironta so'z bo'lmasin.
  'profile.title': 'Kompaniyangiz profili',
  'profile.lead':
    'Bu yerga kiritganingiz har bir tender talabi bilan solishtiriladi. Hech narsa majburiy emas — javobi yo‘q mezon "hali aniqlanmagan" deb ko‘rsatiladi, hech qachon "bajarilmagan" deb emas.',
  'profile.identity': 'Kompaniya',
  'profile.field.name': 'Kompaniya nomi',
  'profile.field.country': 'Mamlakat',
  'profile.financials': 'Moliyaviy holat',
  'profile.financials.hint':
    'Raqamlar AQSh dollarida. Aytishni istamasangiz, katakni bo‘sh qoldiring.',
  'profile.field.turnover': 'O‘rtacha yillik aylanma',
  'profile.field.liquidAssets': 'Likvid aktivlar yoki kredit liniyalari',
  'profile.contracts.title': 'Bajarilgan shartnomalar',
  'profile.contracts.hint':
    'Malaka mezonlari odatda ma’lum summadan yuqori, yaqin yillarda va muvaffaqiyatli yakunlangan shartnomalarni sanaydi. Qo‘shgan har bir shartnoma shu shartlar bo‘yicha hisobga olinadi.',
  'profile.contracts.add': 'Shartnoma qo‘shish',
  'profile.experts.title': 'Asosiy mutaxassislar',
  'profile.experts.hint':
    'Jalb qilmoqchi bo‘lgan mutaxassislar va ularning sohadagi ish staji.',
  'profile.experts.add': 'Mutaxassis qo‘shish',
  'profile.certificates.title': 'Sertifikatlar',
  'profile.certificates.hint': 'Kompaniyangizdagi mavjud sertifikatlar.',
  'profile.certificates.add': 'Sertifikat qo‘shish',
  'profile.field.description': 'Shartnoma nima haqida edi',
  'profile.field.valueUsd': 'Summasi (USD)',
  'profile.field.completedYear': 'Tugallangan yil',
  'profile.field.successfullyCompleted': 'Muvaffaqiyatli yakunlangan',
  'profile.field.expertName': 'Ism-sharif',
  'profile.field.expertRole': 'Lavozimi',
  'profile.field.yearsExperience': 'Ish staji (yil)',
  'profile.field.certificateName': 'Sertifikat',
  'profile.field.issuedYear': 'Berilgan yil',
  'profile.remove': 'O‘chirish',
  'profile.save': 'Profilni saqlash',
  'profile.saving': 'Saqlanmoqda…',
  'profile.saved': 'Saqlandi.',
  'profile.nameRequired': 'Avval kompaniya nomini kiriting.',
  'profile.boolUnset': 'Javob berilmagan',
  'profile.boolYes': 'Ha',
  'profile.boolNo': 'Yo‘q',
  'profile.provisional':
    'Ishchi maydon nomi — tender hujjatlaridagi atama bilan hali solishtirilmagan.',
  'profile.notDeclared': 'Hali hech narsa ko‘rsatilmagan — bu haqdagi mezonlar ochiq qoladi.',
  'profile.declaredNone': 'Sizda bulardan hech biri yo‘qligini bildirdingiz.',
  'profile.declareNone': 'Menda bulardan yo‘q',
  'profile.undeclare': 'Bekor qilish — javobsiz qoldirish',
  'profile.reassure':
    'Bo‘sh maydon — bu biz so‘raydigan savol, sizga qo‘yilgan minus emas.',

  // -- muvofiqlik tekshiruvi -------------------------------------------------
  'check.title': 'Muvofiqlik tekshiruvi',
  'check.lead':
    'Quyidagi har bir talab tenderdan o‘qilgan, so‘ng oddiy arifmetika bilan profilingizga solishtirilgan. Har biri uchun hisob-kitob ko‘rsatilgan.',
  'check.assessedAs': '{name} nomidan baholandi',
  'check.recheck': 'Qayta tekshirish',
  'check.noProfileTitle': 'Hali kompaniya profili yo‘q',
  'check.noProfileBody':
    'Kompaniyangizda nima borligini kiriting — shundan keyin har bir ochiq tenderni shunga solishtirib ko‘rish mumkin.',
  'check.createProfile': 'Profil yaratish',
  'check.status.eligible': 'O‘qiy olgan barcha talablarga javob beradi',
  'check.status.eligibleBody': 'Bu e’londan olingan hech bir mezon sizni chetlatmaydi.',
  'check.status.blocked': 'Bitta talab sizni chetlatadi',
  'check.status.blockedBody':
    'Kiritgan raqamlaringiz bo‘yicha majburiy mezonlardan biri bajarilmayapti.',
  'check.status.incomplete': 'Hali aniqlanmagan',
  'check.status.incompleteBody':
    'Ba’zi mezonlar siz ko‘rsatmagan qiymatni talab qiladi. Uni kiritsangiz, javob aniq bo‘ladi.',
  'check.status.unrated': 'Bu e’londan hali hech narsa ajratib olinmagan',
  'check.status.unratedBody':
    'Bu tenderdan bironta talab o‘qilmagan, shuning uchun hech qanday xulosa chiqarilmagan. Bu na "o‘tdi", na "o‘tmadi" degani.',
  'check.hardGate': 'Majburiy shartlar',
  'check.hardGate.pass': 'O‘tadi',
  'check.hardGate.fail': 'O‘tmaydi',
  'check.hardGate.pending': 'Hal qilinmagan',
  'check.hardGate.pendingHint': 'Majburiy talablardan biri hali sizdan qiymat kutmoqda.',
  'check.coverage': 'Aniq javobi bor mezonlar',
  'check.counts.satisfied': 'Bajarilgan',
  'check.counts.failed': 'Bajarilmagan',
  'check.counts.unknown': 'Sizdan kutilmoqda',
  'check.verdict.satisfied': 'Bajarilgan',
  'check.verdict.failed': 'Bajarilmagan',
  'check.verdict.unknown': 'Hali aniqlanmagan',
  'check.mandatory': 'Majburiy',
  'check.preference': 'Afzallik',
  'check.showOriginal': 'Tenderdagi asl matn',
  'check.hideOriginal': 'Asl matnni yashirish',
  'check.evidence': 'Tenderdan keltirilgan',
  'check.evidenceNone': 'Bu talab uchun iqtibos yozilmagan — unga ehtiyot bo‘lib qarang.',
  'check.source': 'Manba',
  'check.missingTitle': 'Buni hal qilish uchun kerak:',
  'check.missing.scalar': '{label}',
  'check.missing.collection': '{label} — hech narsa ko‘rsatilmagan',
  'check.missing.recordField': '{field} — "{entity}" yozuvlaringizda',
  'check.goToProfile': 'Profilga qo‘shish',
  'check.showWorking': 'Hisob-kitobni ko‘rsatish',
  'check.hideWorking': 'Hisob-kitobni yashirish',
  'check.appliesTo.single': 'Ishtirokchiga nisbatan qo‘llanadi',
  'check.appliesTo.jv_combined': 'Qo‘shma korxonaning barcha a’zolariga birgalikda qo‘llanadi',
  'check.appliesTo.jv_each': 'Qo‘shma korxonaning har bir a’zosiga qo‘llanadi',
  'check.appliesTo.jv_at_least_one': 'Qo‘shma korxonaning kamida bitta a’zosiga qo‘llanadi',
  'check.layer.L1': 'E’londan qoida asosida o‘qilgan',
  'check.layer.L2': 'E’lon matnidan ajratib olingan',
  'check.layer.L3': 'Tender hujjatidan ajratib olingan',
  'check.grounding.verified': 'Iqtibos manbadan topildi',
  'check.grounding.unchecked': 'Iqtibos hali tekshirilmagan',
  'check.emptyTitle': 'Bu e’londan talablar o‘qilmagan',
  'check.emptyBody':
    'Ko‘p e’lonlarda malaka mezonlari umuman keltirilmaydi. Bunday holatda tender hujjatining o‘zini o‘qish kerak, bu e’lon uchun esa bu hali qilinmagan.',

  // -- Hujjatni mijozning o'zi taqdim etadi -----------------------------------
  'check.supply.title': 'Tender hujjatlari',
  'check.supply.body':
    'Bu e’londa malaka mezonlari keltirilmagan. Ular tender hujjatida bo‘ladi, e’lon esa unga havola bermaydi — shuning uchun bu yerda hali hech narsa o‘qilmagan. E’londa aloqa ma’lumoti aynan shu hujjatni so‘rash uchun chop etiladi.',
  'check.supply.bodyPartial':
    'Yuqoridagi talablar mana shu hujjatlardan o‘qilgan. Agar buyurtmachi sizga bizda yo‘q hujjatni yuborgan bo‘lsa, uni o‘z uyachasiga tashlang — talablar qaytadan o‘qiladi.',
  'check.supply.slot.tor': 'Texnik topshiriq (TOR)',
  'check.supply.slot.torHint': 'TOR yoki REOI hujjati — topshiriq nima va unga kim taklif bera oladi.',
  'check.supply.slot.rfp': 'RFP / tender hujjati',
  'check.supply.slot.rfpHint': 'Takliflar so‘rovi (RFP) yoki tender hujjati — malaka bo‘limi shu yerda bo‘ladi.',
  'check.supply.slot.other': 'Boshqa hujjat',
  'check.supply.slot.otherHint': 'Qo‘shimcha, aniqlik kiritish, ilova — buyurtmachi yuborgan va shart bayon qilgan har qanday hujjat.',
  'check.supply.slot.held': 'Bor',
  'check.supply.slot.chars': '{count} belgi o‘qildi',
  'check.supply.slot.add': 'Fayl tanlang',
  'check.supply.slot.replace': 'Yana qo‘shish',
  'check.supply.slot.link': 'Havola qo‘ying',
  'check.supply.slot.complete': 'Bu yerga boshqa hujjat kerak emas.',
  'check.supply.origin.harvested': 'E’londan olingan',
  'check.supply.origin.supplied': 'Ta’minotchi yuborgan',
  'check.supply.contactTitle': 'Hujjatni so‘rang',
  'check.supply.contact.name': 'Mas’ul shaxs',
  'check.supply.contact.organization': 'Tashkilot',
  'check.supply.contact.email': 'Elektron pochta',
  'check.supply.contact.phone': 'Telefon',
  'check.supply.contact.address': 'Manzil',
  'check.supply.contact.web': 'Veb-sayt',
  'check.supply.urlPlaceholder': 'https://… — Google Drive yoki Docs havolasi ham bo‘ladi',
  'check.supply.submit': 'Hujjatni o‘qish',
  'check.supply.working': 'Hujjat o‘qilmoqda…',
  'check.supply.unreadable': 'Hujjat keldi, lekin o‘qib bo‘lmadi: {problem}',
  'check.supply.foundNothing':
    'Hujjat o‘qildi, lekin unda birorta malaka mezoni topilmadi.',
  'check.supply.found_one': 'Hujjatdan {count} ta talab o‘qildi.',
  'check.supply.found_other': 'Hujjatdan {count} ta talab o‘qildi.',
  'check.supply.privacy':
    'Hujjat shu yerda saqlanadi va o‘qiladi. Tarqatishga haqqingiz yo‘q narsani yubormang.',
  'check.withheld_one':
    'Ajratib olingan {count} ta talab e’tiborga olinmadi: uning iqtibosi manbadan topilmadi.',
  'check.withheld_other':
    'Ajratib olingan {count} ta talab e’tiborga olinmadi: ularning iqtiboslari manbadan topilmadi.',
  'check.openTender': 'Tenderni ochish',

  // -- Tayyorlik darajasi -----------------------------------------------------
  'check.readFrom': 'Manba:',
  'check.readFrom.noneHeld': 'faqat e’lon matni',
  'check.readFrom.noTor': 'Texnik topshiriq (TOR) hali yo‘q — quyidagi ro‘yxat to‘liq bo‘lmasligi mumkin.',
  'check.readiness.title': 'Ushbu tenderning qancha qismini tasdiqladingiz',
  'check.readiness.ceiling': 'qolgani hal bo‘lsa — {value} gacha',
  'check.readiness.counts': '{total} talabdan {satisfied} tasi bajarilgan',
  'check.readiness.open':
    'Bunga faqat siz tasdiqlagan talablar kiradi. Chiziqning ochroq qismi — hali javob berilmagani.',
  'check.readiness.settled': 'Ushbu tenderdan o‘qilgan har bir talabga javob berilgan.',
  'check.readiness.blocked':
    'Majburiy talab bajarilmagan. Bu ko‘rsatkich qanchalik yuqori bo‘lmasin, shu holat o‘zgarmaguncha taklif to‘sib qo‘yilgan.',
  'check.importance.high': 'Ishtirokni hal qiladi',
  'check.importance.medium': 'Talab qilinadi',
  'check.importance.low': 'Afzallik',

  'check.declare.question': 'Kompaniyangiz bu shartga javob beradimi?',
  'check.declare.yes': 'Bizda bor',
  'check.declare.no': 'Bizda yo‘q',
  'check.declare.clear': 'Javobni bekor qilish',
  'check.declare.state.yes': 'Bizda bor',
  'check.declare.state.no': 'Bizda yo‘q',
  'check.declare.state.unset': 'Hali javob berilmagan',
  'check.declare.byYou': 'Siz javob berdingiz — profilingizdan hisoblanmagan.',
  // -- Talablar yonidagi tender matni ------------------------------------------
  'viewer.title': 'Tender matni',
  'viewer.loading': 'Tender ochilmoqda…',
  'viewer.showInSource': 'Qayerdaligini ko‘rsat',
  'viewer.hideInSource': 'Ko‘rsatishni to‘xtat',
  'viewer.notLocated':
    'Bu talabning jumlasi quyidagi matndan topilmadi. Kartadagi iqtibos baribir tasdiqlangan iqtibos.',
  'viewer.problem.none': 'Bu e’lon uchun hozircha o‘qiladigan tender matni yo‘q.',
  'viewer.problem.noTextLayer':
    'Tender hujjati — matn qatlamisiz skan, shuning uchun unda hech narsani ko‘rsatib bo‘lmaydi.',
  'viewer.problem.fileMissing': 'Tender hujjati bu serverda mavjud emas.',
  'viewer.problem.parserUnavailable':
    'Bu o‘rnatmada hujjatni ajratib ko‘rsatish uchun ocholmaymiz.',

  'check.disclaimer':
    'Bu e’lonni siz kiritgan ma’lumot bilan solishtiradi. Bu ariza topshirish emas va tender hujjatini o‘qish o‘rnini bosmaydi.',
  'detail.checkEligibility': 'Muvofiqligimni tekshirish',
  'layout.profile': 'Mening profilim',

  // -- vendor hisoblari ------------------------------------------------------
  'auth.signInTitle': 'Kirish',
  'auth.signInBody':
    'Profilingiz va tekshiruvlaringiz hisobingizda saqlanadi — istalgan qurilmadan qoldirgan joyingizdan davom etasiz.',
  'auth.registerTitle': 'Hisob yaratish',
  'auth.registerBody':
    'Kim ekaningizni bir marta yozasiz. Shundan keyin har bir tenderni siz ko‘rsatgan ma’lumot bilan solishtirib beramiz, siz esa faqat kompaniyangizda o‘zgarish bo‘lganda yangilaysiz.',
  'auth.company': 'Kompaniya nomi',
  'auth.country': 'Davlat',
  'auth.email': 'Elektron pochta',
  'auth.password': 'Parol',
  'auth.passwordHint': 'Kamida 8 belgi, va juda ommabop parollardan bo‘lmasin.',
  'auth.signInAction': 'Kirish',
  'auth.registerAction': 'Hisob yaratish',
  'auth.working': 'Kuting…',
  'auth.noAccount': 'Hali hisobingiz yo‘qmi?',
  'auth.haveAccount': 'Hisobingiz bormi?',
  'auth.privacy':
    'Siz kiritgan ma’lumot faqat sizning tekshiruvlaringizga javob berish uchun ishlatiladi.',
  'auth.backHome': 'Tenderlarga qaytish',
  'auth.signOut': 'Chiqish',
  'auth.signedInAs': '{email} sifatida kirgansiz',
  'auth.needAccountTitle': 'Muvofiqlikni tekshirish uchun kiring',
  'auth.needAccountBody':
    'Tekshiruv bu tenderni kompaniyangiz ko‘rsatgan ma’lumot bilan solishtiradi, shuning uchun hisob kerak. Tender nima talab qilishini o‘qish uchun esa kerak emas.',
  'auth.goToSignIn': 'Kirish yoki hisob yaratish',

  // -- API xatoliklari, backend kodi bo'yicha --------------------------------
  'error.network':
    'Serverga ulanib bo‘lmadi. Internet aloqangizni tekshirib, qayta urinib ko‘ring.',
  'error.throttled': 'So‘rovlar juda ko‘p. Bir oz kutib, qayta urinib ko‘ring.',
  'error.http': 'So‘rov bajarilmadi ({status}-holat kodi).',
  'error.unknown': 'Nimadir noto‘g‘ri ketdi.',
  'error.not_found': 'So‘ralgan yozuv topilmadi.',
  'error.permission_denied': 'Bu amalni bajarishga ruxsatingiz yo‘q.',
  'error.not_authenticated': 'Davom etish uchun tizimga kiring.',
  'error.method_not_allowed': 'Bu so‘rov usuli bu yerda qo‘llab-quvvatlanmaydi.',
  'error.invalid': 'So‘rov noto‘g‘ri deb rad etildi.',
  'error.parse_error': 'So‘rov tanasini o‘qib bo‘lmadi.',
  'error.service_unavailable':
    'Bog‘liq xizmat ishlamayapti. Birozdan so‘ng qayta urinib ko‘ring.',
  'error.server': 'Serverda kutilmagan xatolik yuz berdi. Birozdan so‘ng urinib ko‘ring.',

  // -- semantik qidiruv ------------------------------------------------------
  'layout.search': 'Qidiruv',
  'search.heading': 'Arxiv bo‘yicha qidiruv',
  'search.subtitle':
    'So‘z bilan so‘rang — e’lon matnlari va ular havola qilgan tender hujjatlari bo‘yicha. Har bir natija o‘zi olingan sahifani ochadi va parcha belgilab ko‘rsatiladi.',
  'search.placeholder': 'masalan: avans to‘lovi kafolati, minimal yillik aylanma…',
  'search.submit': 'Qidirish',
  'search.filter.category': 'Yo‘nalish',
  'search.filter.allCategories': 'Barcha yo‘nalishlar',
  'search.noResults': 'Hech narsa topilmadi',
  'search.noResultsHint': 'Kamroq so‘z bilan yoki boshqa yo‘nalishda urinib ko‘ring.',
  'search.retrieval.vector': 'ma’noviy moslik · {score}',
  'search.retrieval.fts': 'kalit so‘z mosligi · {score}',
  'search.badge.page': '{page}-sahifani ochish',
  'search.badge.text': 'Manbani ochish',
  'search.degraded.embeddings':
    'Bu deploymentda semantik indeks o‘chirilgan, shuning uchun bular e’lon matnlaridagi kalit so‘z mosliklari — nusxalangan hujjatlar bo‘yicha qidiruv emas.',
  'search.degraded.store':
    'Semantik indeksga ulanib bo‘lmadi, shuning uchun bular e’lon matnlaridagi kalit so‘z mosliklari. Hech narsa yo‘qolmaydi — indeksni qayta qurish mumkin.',
  'search.degraded.keyword':
    'Semantik indeks bu savolga hech narsa qaytarmadi, shuning uchun kalit so‘z mosliklari ko‘rsatilmoqda.',
  'search.citation.title': 'Ushbu parchaning manbasi',
  'search.citation.notice': 'Tender',
  'search.citation.page': 'sahifa',
  'search.citation.close': 'Yopish',
  'search.citation.loading': 'Manba ochilmoqda…',
  'search.citation.fileUnavailable':
    'Hujjatning o‘zi bu serverda mavjud emas. Parcha quyida ko‘rsatilgan.',
  'search.citation.sourceUnavailable':
    'Atrofdagi matnni yuklab bo‘lmadi. Parcha quyida ko‘rsatilgan.',
  'search.citation.resize': 'Manba panelining kengligini o‘zgartirish',
  'search.citation.resizeHint':
    'Kengaytirish uchun suring · dastlabki holatga qaytarish uchun ikki marta bosing',

  // -- chat + semantik qo'shnilar --------------------------------------------
  'chat.title': 'Arxivdan so‘rang',
  'chat.close': 'Yopish',
  'chat.send': 'So‘rash',
  'chat.placeholder': 'Talablar, muddatlar, hujjatlar haqida so‘rang…',
  'chat.scopedToTender': 'Shu tender bo‘yicha javob beradi',
  'chat.scopedToArchive': 'Butun nusxa bo‘yicha javob beradi',
  'chat.intro':
    'Javobdagi har bir gap o‘zi olingan parchani olib yuradi. Raqamni bosing — parcha qarz oluvchining o‘z hujjatida ochiladi.',
  'chat.example1': 'Bu tender qanday moliyaviy talablar qo‘yadi?',
  'chat.example2': 'Qo‘shma korxonaga ruxsat berilganmi, qanday shartlarda?',
  'chat.example3': 'Taklif bilan birga qanday hujjatlar topshiriladi?',
  'chat.thinking': 'Arxiv o‘qilmoqda…',
  'chat.openSource': 'Manba parchasini ochish',
  'chat.nothingFound': 'Arxivda bunga javob beradigan narsa topilmadi.',
  'chat.noAnswerButSources':
    'Bu parchalardan javob yozib bo‘lmadi, lekin parchalarning o‘zi mana.',
  'chat.keywordAnswer':
    'Semantik indeks bunga javob bermadi, shuning uchun bu gaplar kalit so‘z mosliklari ustiga yozilgan — asos kuchsizroq.',
  'chat.dropped_one':
    '{count} ta gap olib tashlandi — u manbalardagi hech narsaga tayanmagan.',
  'chat.dropped_other':
    '{count} ta gap olib tashlandi — ular manbalardagi hech narsaga tayanmagan.',
  'chat.degraded.model': 'Javob beruvchi model sozlanmagan, shuning uchun faqat parchalar ko‘rsatildi.',
  'chat.degraded.failed': 'Javob beruvchi model ishlamadi; parchalarga bu ta’sir qilmaydi.',
  'layout.chat': 'Suhbat',
  'chat.pageTitle': 'Arxiv bilan suhbat',
  'chat.openFull': 'To‘liq oynada ochish',
  'chat.newThread': 'Yangi suhbat',
  'chat.threads': 'Suhbatlar',
  'chat.showThreads': 'Suhbatlar ro‘yxatini ochish',
  'chat.hideThreads': 'Suhbatlar ro‘yxatini yig‘ish',
  'chat.citeRecord': 'Bu manba — bazamizdagi qator, hujjat emas. Tenderni ochadi.',
  'chat.citeRecordList': 'Bu manba — bazamizdagi qator, hujjat emas. Ochiq tenderlar ro‘yxatini ochadi.',
  'chat.noThreads': 'Hozircha suhbat yo‘q. Savol bering — u shu yerda saqlanadi.',
  'chat.untitled': 'Nomsiz suhbat',
  'chat.wholeArchive': 'Butun arxiv',
  'chat.rename': 'Nomini o‘zgartirish',
  'chat.delete': 'O‘chirish',
  'chat.welcomeTitle': 'Nima haqida so‘raymiz?',
  'chat.loadingOlder': 'Oldingi xabarlar yuklanmoqda…',
  'chat.stage.retrieving': 'Savol vektorga aylantirilmoqda…',
  'chat.stage.reading_one': '{count} manba o‘qilmoqda…',
  'chat.stage.reading_other': '{count} ta manba o‘qilmoqda…',
  'chat.stage.claims_one': '{count} ta gap yozildi…',
  'chat.stage.claims_other': '{count} ta gap yozildi…',
  'chat.stage.writing': 'Javob yozilmoqda…',
  'chat.grounding':
    'Har bir gap o‘zi tayangan hujjat parchasiga bog‘langan — raqamni bosib, asl matnni ko‘ring.',
  'chat.degraded.index': 'Semantik indeks mavjud emas, bular kalit so‘z mosliklari.',

  'similar.openPassage': 'Bu gapni o‘zi olingan e’londa ochish',
  'similar.scoreHint':
    'Shu tenderga kosinus o‘xshashligi. O‘qib tekshirib bo‘lmaydi — keltirilgan gapni esa mumkin.',
};

export default uz;
