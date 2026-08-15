/**
 * English catalogue — the canonical key set.
 *
 * Every other language file is typed against this one, so a missing or
 * misspelled key is a compile error rather than a string that silently falls
 * back at runtime.
 *
 * Plural keys carry an `Intl.PluralRules` category suffix (`_one`, `_few`,
 * `_many`, `_other`). Only `_other` is mandatory; the resolver falls back to it
 * for any category the language does not define.
 */
const en = {
  // -- document / brand ------------------------------------------------------
  'meta.title': 'Pintell — Global procurement notices',
  'meta.description':
    'Browse World Bank procurement notices from around the world in one clean interface.',
  'brand.tagline': 'Global procurement notices',

  // -- layout ----------------------------------------------------------------
  'layout.homeAria': 'Pintell home',
  'layout.allTenders': 'All tenders',
  'layout.switchToLight': 'Switch to the light theme',
  'layout.switchToDark': 'Switch to the dark theme',
  'layout.languageAria': 'Interface language',
  'layout.companies': 'Companies',
  'layout.awards': 'Awarded contracts',
  'layout.experts': 'Experts',

  // -- expert directory ------------------------------------------------------
  'experts.title': 'Expert directory',
  'experts.lead':
    'People who work the roles consulting tenders name — a Team Leader, a Resettlement Specialist, an Auditor. A tender that names a role you cannot fill is one you cannot bid on, so the directory sits beside the tenders rather than somewhere else.',
  'experts.filterAria': 'Filter experts',
  'experts.searchPlaceholder': 'Name…',
  'experts.role': 'Role',
  'experts.allRoles': 'All roles',
  'experts.family': 'Field',
  'experts.allFamilies': 'All fields',
  'experts.sortBy': 'Sort by',
  'experts.sortName': 'Name (A–Z)',
  'experts.sortNameDesc': 'Name (Z–A)',
  'experts.sortUpdated': 'Recently updated',
  'experts.count_one': '{count} expert',
  'experts.count_other': '{count} experts',
  'experts.colName': 'Name',
  'experts.colRoles': 'Roles',
  'experts.colProfile': 'Profile',
  'experts.profileLink': 'LinkedIn',
  'experts.noProfile': 'No link on file',
  'experts.emptyTitle': 'Nobody listed for these filters',
  'experts.emptyBody':
    'The directory is curated by hand and is still filling up. Try a wider field, or clear the search box.',
  'experts.clear': 'Clear filters',

  // -- the team a tender names -----------------------------------------------
  'noticeExperts.title': 'Experts this tender names',
  'noticeExperts.lead':
    'Positions the tender text itself asks the bidding team to include. Each carries the sentence that asks for it.',
  'noticeExperts.none':
    'This tender names no expert positions in the text we have read.',
  'noticeExperts.mandatory': 'Required',
  'noticeExperts.desirable': 'Desirable',
  'noticeExperts.needed': '{count} needed',
  'noticeExperts.unfiled': 'Not in our directory',
  'noticeExperts.candidates': 'From our directory',
  'noticeExperts.noCandidates': 'Nobody listed for this role yet.',
  'noticeExperts.candidatesNote':
    'Suggestions from our own directory, not from the tender. Nobody here has been assessed against it.',
  'noticeExperts.seeAll': 'See all in the directory',
  'noticeExperts.withheld_one': '{count} position withheld: its quote was not found in the source.',
  'noticeExperts.withheld_other': '{count} positions withheld: their quotes were not found in the source.',

  // -- awarded contracts (who won, and who else was in the room) --------------
  'awards.title': 'Contracts already decided',
  'awards.lead':
    'Who won, who else was evaluated, and who was rejected — as each notice published it. Every row links back to its source page.',
  'awards.filterAria': 'Filter awarded contracts',
  'awards.searchPlaceholder': 'Company or contract…',
  'awards.role': 'Show contracts that name',
  'awards.roleAll': 'Any outcome',
  'awards.roleEvaluated': 'Other bidders evaluated',
  'awards.roleRejected': 'Bidders rejected',
  'awards.count': '{count} contracts',
  'awards.awardee': 'Won by',
  'awards.evaluated': 'Also evaluated',
  'awards.rejected': 'Rejected',
  'awards.openNotice': 'Open the notice',
  'awards.openUpstream': 'Source page',
  'awards.emptyTitle': 'No contracts match',
  'awards.emptyBody': 'Try a wider direction or clear the country filter.',
  'similar.title': 'Contracts already awarded in this line of work',
  'similar.lead':
    'Decided contracts in the same line of work, newest first. Shown for context — it says nothing about this tender.',
  'similar.note':
    'Selected on the line of work alone, and ordered by award date. Not ranked, and not a prediction.',
  'similar.noWinner': 'Winner not named',
  'similar.alsoBid': 'Also in the running:',
  'similar.openAward': 'Open the contract',
  'similar.openUpstream': 'Source page',
  'similar.role.evaluated': '(evaluated)',
  'similar.role.rejected': '(rejected)',

  // -- companies (competitor roster) -----------------------------------------
  'companies.title': 'Companies winning contracts',
  'companies.lead':
    'Suppliers that have won World Bank contracts, drawn from published award notices. Ordered by number of wins — not a ranking: most companies here appear once, so the counts are shown rather than turned into a score.',
  'companies.filterAria': 'Filter companies',
  'companies.searchPlaceholder': 'Company name…',
  'companies.sortBy': 'Sort by',
  'companies.sortWins': 'Most wins',
  'companies.sortLatest': 'Most recent win',
  'companies.sortValue': 'Highest value (USD)',
  'companies.sortName': 'Name (A–Z)',
  'companies.count_one': '{count} company',
  'companies.count_other': '{count} companies',
  'companies.colName': 'Company',
  'companies.colCountry': 'Country',
  'companies.colWins': 'Wins',
  'companies.colValue': 'Value (USD)',
  'companies.colLatest': 'Latest win',
  'companies.ofWins': 'across {count} of {total}',
  'companies.emptyTitle': 'No companies match these filters',
  'companies.emptyBody': 'Try a different direction, or clear the search box.',
  'companies.back': 'Back to companies',
  'companies.firstAward': 'First win',
  'companies.byCategory': 'By direction',
  'companies.byCountry': 'By country',
  'companies.awards': 'Contracts won',
  'layout.dataSource': 'Data source:',
  'layout.dataSourceName': 'World Bank Group Procurement Notices',
  'layout.disclaimer':
    'Pintell mirrors public World Bank data and is not affiliated with or endorsed by the World Bank Group.',

  // -- tender list -----------------------------------------------------------
  'list.titleFocus': 'Open tenders in your region',
  'list.titleArchive': 'Global tender notices',
  'list.leadFocus':
    'Live opportunities across CIS countries and Afghanistan, categorised by direction so you only follow the work you actually bid on. Refreshed every 30 minutes.',
  'list.leadArchive':
    'The full World Bank procurement archive, mirrored locally and refreshed automatically every 30 minutes.',
  'list.stat.openOpportunities': 'Open opportunities',
  'list.stat.closingToday': 'Closing today',
  'list.stat.countries': 'Countries',
  'list.stat.categorised': 'Categorised',
  'list.stat.latestNotice': 'Latest notice',
  'list.stat.noticesMirrored': 'Notices mirrored',
  'list.stat.currentlyOpen': 'Currently open',
  'list.stat.countriesRegions': 'Countries & regions',
  'list.stat.archiveBackTo': 'Archive back to',
  'list.empty.title': 'No tenders match these filters',
  'list.empty.focus':
    'Nothing open in the focus region right now. Widen the filters, or switch off the focus toggle to search the whole archive.',
  'list.empty.archive':
    'Try widening the country or method filter, or clear the search box.',

  // -- archive import banner -------------------------------------------------
  'archive.importing_one':
    'Importing the historical archive — {percent}% ({done}/{total} partition)',
  'archive.importing_other':
    'Importing the historical archive — {percent}% ({done}/{total} partitions)',
  'archive.stored_one': '{stored} notice stored',
  'archive.stored_other': '{stored} notices stored',
  'archive.storedOf_one': '{stored} of {total} notice stored',
  'archive.storedOf_other': '{stored} of {total} notices stored',

  // -- filters ---------------------------------------------------------------
  'filter.aria': 'Filter tenders',
  'filter.focusLabel': '{group} · open opportunities only',
  'filter.focusFallbackGroup': 'Focus region',
  'filter.focusHint':
    'Requests for Expression of Interest and Invitations for Bids whose submission deadline has not passed.',
  'filter.countriesAria': 'Countries in the focus region',
  'filter.search': 'Search',
  'filter.searchPlaceholder': 'Description, project, reference number…',
  'filter.category': 'Direction',
  'filter.allCategories': 'All directions',
  'filter.subcategory': 'Consulting focus',
  'filter.allSubcategories': 'All consulting work',
  'subcategory.engineering': 'Engineering, design & supervision',
  'subcategory.audit': 'Audit & financial management',
  'subcategory.environment_social': 'Environmental & social',
  'subcategory.training': 'Training & capacity building',
  'subcategory.research': 'Studies, research & evaluation',
  'subcategory.it_advisory': 'IT & digital advisory',
  'subcategory.legal_procurement': 'Legal & procurement advisory',
  'subcategory.management': 'Project management support',
  'subcategory.other': 'Other consulting',
  'filter.audience': 'Bidder type',
  'filter.allAudiences': 'Firms and individuals',
  'audience.firm': 'Consulting firms',
  'audience.individual': 'Individual consultants',

  // Short forms for the card chip. No `other`: it is never shown, because
  // it says nothing the category tag did not already say.
  'subcategoryShort.engineering': 'Engineering',
  'subcategoryShort.audit': 'Audit',
  'subcategoryShort.environment_social': 'Environment',
  'subcategoryShort.training': 'Training',
  'subcategoryShort.research': 'Research',
  'subcategoryShort.it_advisory': 'IT advisory',
  'subcategoryShort.legal_procurement': 'Legal',
  'subcategoryShort.management': 'Management',
  'filter.country': 'Country',
  'filter.allCountries': 'All countries',
  'filter.method': 'Procurement method',
  'filter.allMethods': 'All methods',
  'filter.noticeType': 'Notice type',
  'filter.allTypes': 'All types',
  'filter.deadlineStatusAria': 'Deadline status',
  'filter.statusAll': 'All',
  'filter.statusOpen': 'Open',
  'filter.statusClosed': 'Closed',
  'filter.resultCount_one': '{count} notice',
  'filter.resultCount_other': '{count} notices',
  'filter.clear': 'Clear filters',

  // -- tender card -----------------------------------------------------------
  'card.noticeFallback': 'Notice',
  'card.country': 'Country',
  'card.method': 'Method',
  'card.published': 'Published',
  'card.deadline': 'Deadline',
  'card.viewDetails': 'View details',

  // -- tender detail ---------------------------------------------------------
  'detail.back': 'Back to tenders',
  'detail.noticeText': 'Notice text',
  'detail.contact': 'Contact',
  'detail.keyFacts': 'Key facts',
  'detail.fact.country': 'Country / region',
  'detail.fact.deadline': 'Deadline',
  'detail.fact.deadlineTime': 'Deadline time',
  'detail.fact.published': 'Published',
  'detail.fact.method': 'Procurement method',
  'detail.fact.methodCode': 'Method code',
  'detail.fact.reference': 'Reference',
  'detail.fact.projectId': 'Project ID',
  'detail.fact.noticeId': 'Notice ID',
  'detail.fact.language': 'Language',
  'detail.openUpstream': 'Open on worldbank.org →',
  'detail.mirrored': 'Mirrored {when}',
  'detail.winChance': 'Win probability',
  'detail.winChanceSoon': 'Coming soon',
  'detail.showFullText': 'Read the full notice',
  'detail.hideFullText': 'Collapse',
  'detail.budget': 'Project budget',
  'detail.agency': 'Implementing agency',
  'detail.overview': 'At a glance',
  'detail.bankContact': 'World Bank team',
  'detail.bankContactHint': 'Accountable for the project, not the tender',
  'detail.subConfidence': 'Sub-direction confidence: {percent}',
  'detail.moreContact': 'More contact detail',

  // -- terms of reference, read out of the notice body -----------------------
  'tor.title': 'Terms of Reference',
  'tor.open': 'Open the TOR →',
  'tor.fromNotice': 'Link published in the notice text.',
  'tor.request': 'Request the TOR by e-mail',
  'tor.requestHint': 'No link was published; the notice asks you to write to {email}.',
  'tor.mailSubject': 'Request for the Terms of Reference — {title}',
  'tor.mentionedOnly': 'The notice refers to a TOR but publishes neither a link nor an address. Read the full text below.',
  'tor.biddingDocument': 'Bidding document',
  'tor.otherLink': 'Link from the notice',

  // -- deadline countdown ----------------------------------------------------
  'countdown.label': 'Submission deadline',
  'countdown.days': 'd',
  'countdown.hours': 'h',
  'countdown.minutes': 'm',
  'countdown.seconds': 's',
  'countdown.closed': 'Closed',
  'countdown.approximate':
    'Time shown for {zone}; this country spans several zones — confirm at the source.',

  'contact.organization': 'Organization',
  'contact.name': 'Contact',
  'contact.email': 'Email',
  'contact.phone': 'Phone',
  'contact.address': 'Address',
  'contact.country': 'Country',
  'contact.website': 'Website',

  // -- the three contact tiers ------------------------------------------------
  'contacts.title': 'Who to contact',
  'contacts.tier.notice': 'Published contact',
  'contacts.tier.noticeHint': 'Named in the notice’s own contact fields.',
  'contacts.tier.body': 'Also named in the notice text',
  'contacts.tier.bodyHint':
    'Read out of the address block at the end of the notice — often where bids actually go.',
  'contacts.tier.bank': 'World Bank team',
  'contacts.tier.bankHint':
    'Accountable for the project at the Bank, not for this tender. Go through the borrower above first.',
  'contacts.purpose.submission': 'Send submissions here',
  'contacts.purpose.enquiry': 'Questions',
  'contacts.purpose.tor': 'Request the TOR',
  'contacts.alsoAt': 'Also reachable at',
  'contacts.samePerson': 'Same person as above, different address',
  'contacts.unnamed': 'Address published without a name',
  'contacts.emailUnconfirmed': 'Unconfirmed — follows the Bank’s staff address pattern',
  'contacts.emailConfirmed': 'Published on a World Bank page',
  'contacts.emailFromEsrs': 'Published in the project’s ESRS',
  'contacts.noEmail': 'No address published',
  'contacts.parsedNote': 'Read automatically from the notice text — check it against the full notice below.',
  'contacts.profileLink': 'Profile ↗',
  'contacts.publicationLink': 'Publications ↗',
  'contacts.otherLink': 'Link ↗',

  // -- team lead detail page --------------------------------------------------
  'lead.published': 'Published profile',
  'lead.title': 'Job title',
  'lead.unit': 'Unit',
  'lead.office': 'Duty station',
  'lead.projects': 'Projects',
  'lead.notices': 'Tenders',
  'lead.openNotices': 'Open now',
  'lead.projectsTitle': 'Projects they lead',
  'lead.noticesTitle': 'Tenders from those projects',
  'lead.noProjects': 'No mirrored project names this person as a team lead.',
  'lead.noNotices': 'No tenders have been mirrored for these projects yet.',
  'lead.open': 'Open',
  'lead.checked': 'Looked up {when}',
  'lead.notCheckedYet': 'Nobody has looked this person up yet — only their name is known. Run the team-lead enrichment to fill this in.',
  'lead.scopeTitle': 'What this page shows',
  'lead.scopeNote': 'Professional information published by the World Bank only: job title, unit, duty station, work address and public professional pages. Personal social accounts, messaging handles and photographs are not collected.',
  'lead.viewProfile': 'View profile →',
  'lead.bankPage': 'World Bank staff page ↗',


  // -- contract award --------------------------------------------------------
  'award.title': 'Contract award',
  'award.awardedTo': 'Awarded to',
  'award.notPublished': 'Not published',
  'award.companyWebsite': 'Company website ↗',
  'award.contractPrice': 'Signed contract price',
  'award.evaluatedPrice': 'Evaluated bid price',
  'award.bidPriceOpening': 'Bid price at opening',
  'award.awardDate': 'Award notified',
  'award.duration': 'Duration',
  'award.otherBidders': 'Other evaluated bidders',
  'award.unnamedBidder': 'Unnamed bidder',
  'award.websiteNote':
    'The company website was found automatically by web search — verify before relying on it.',

  // -- project documents -----------------------------------------------------
  'project.title': 'Project {id}',
  'project.loadError': 'Project documents could not be loaded: {error}',
  'project.notMirrored':
    'This project has not been mirrored yet — its documents will appear here after the next enrichment cycle.',
  'project.untitled': 'Untitled project',
  'project.page': 'Project page ↗',
  'project.status': 'Status',
  'project.totalCost': 'Total project cost',
  'project.commitment': 'Commitment',
  'project.implementingAgency': 'Implementing agency',
  'project.instrument': 'Instrument',
  'project.closingDate': 'Closing date',
  'project.documents': 'Project documents',
  'project.downloadPdf': 'Download the PDF',
  'project.olderRevisions_one': '{count} earlier revision',
  'project.olderRevisions_other': '{count} earlier revisions',
  'project.undatedRevision': 'Undated version',
  'project.otherDocuments_one': '{count} further document (administrative)',
  'project.otherDocuments_other': '{count} further documents (administrative)',
  'project.noDocuments': 'No documents have been published for this project.',
  'project.untitledDocument': 'Untitled document',

  // -- shared states ---------------------------------------------------------
  'state.errorTitle': 'Something went wrong',
  'state.tryAgain': 'Try again',
  'state.loadingTenders': 'Loading tenders',
  'state.loadingTender': 'Loading tender',
  'notice.noText': 'No notice text was published for this tender.',

  'notFound.title': 'Page not found',
  'notFound.description': 'That address does not match anything in Pintell.',

  'pagination.aria': 'Pagination',
  'pagination.previous': '← Previous',
  'pagination.next': 'Next →',
  'pagination.page': 'Page {page}',

  // -- relative dates --------------------------------------------------------
  'deadline.none': 'No deadline published',
  'deadline.left_one': '{count} day left',
  'deadline.left_other': '{count} days left',
  'deadline.hours_one': '{count} hour left',
  'deadline.hours_other': '{count} hours left',
  'deadline.lastHour': 'Less than an hour left',
  'deadline.today': 'Closes today',
  'deadline.yesterday': 'Closed yesterday',
  'deadline.ago_one': 'Closed {count} day ago',
  'deadline.ago_other': 'Closed {count} days ago',

  // -- tender directions -----------------------------------------------------
  'category.construction': 'Construction & works',
  'category.consulting': 'Consulting',
  'category.supply': 'Supply of goods',
  'category.services': 'Services',
  'category.it': 'IT & digital',
  'category.other': 'Other',
  'category.unknown': 'Unclassified',

  'categoryShort.construction': 'Construction',
  'categoryShort.consulting': 'Consulting',
  'categoryShort.supply': 'Supply',
  'categoryShort.services': 'Services',
  'categoryShort.it': 'IT',
  'categoryShort.other': 'Other',
  'categoryShort.unknown': 'Unclassified',

  'categorySource.agent': 'Read and classified by an agent',
  'categorySource.ai': 'Classified by AI from the notice document',
  'categorySource.rules': 'Classified from the notice wording',
  'categorySource.manual': 'Set manually by an operator',
  'categorySource.none': 'Not classified yet',

  // -- vendor profile --------------------------------------------------------
  // Wording rule for this whole section: a field the vendor has not filled in
  // is *unanswered*, never *failed*. Nothing here may imply that leaving a box
  // empty counts against them.
  'profile.title': 'Your company profile',
  'profile.lead':
    'What you enter here is compared against what each tender requires. Nothing is mandatory — a criterion we have no answer for is reported as "not established yet", never as a failure.',
  'profile.identity': 'Company',
  'profile.field.name': 'Company name',
  'profile.field.country': 'Country',
  'profile.financials': 'Financial standing',
  'profile.financials.hint': 'Figures in USD. Leave a box empty if you would rather not say.',
  'profile.field.turnover': 'Average annual turnover',
  'profile.field.liquidAssets': 'Liquid assets or credit lines',
  'profile.contracts.title': 'Completed contracts',
  'profile.contracts.hint':
    'Qualification criteria usually count contracts above a value, within a recent period, that were finished successfully. Each contract you add is counted against those conditions.',
  'profile.contracts.add': 'Add a contract',
  'profile.experts.title': 'Key experts',
  'profile.experts.hint': 'People you would assign, and how long they have worked in the field.',
  'profile.experts.add': 'Add an expert',
  'profile.certificates.title': 'Certificates',
  'profile.certificates.hint': 'Certifications your company holds.',
  'profile.certificates.add': 'Add a certificate',
  'profile.field.description': 'What the contract was',
  'profile.field.valueUsd': 'Value (USD)',
  'profile.field.completedYear': 'Year completed',
  'profile.field.successfullyCompleted': 'Completed successfully',
  'profile.field.expertName': 'Name',
  'profile.field.expertRole': 'Role',
  'profile.field.yearsExperience': 'Years of experience',
  'profile.field.certificateName': 'Certificate',
  'profile.field.issuedYear': 'Year issued',
  'profile.remove': 'Remove',
  'profile.save': 'Save profile',
  'profile.saving': 'Saving…',
  'profile.saved': 'Saved.',
  'profile.nameRequired': 'Enter a company name first.',
  'profile.boolUnset': 'Not answered',
  'profile.boolYes': 'Yes',
  'profile.boolNo': 'No',
  'profile.provisional':
    'Working field name — not yet reconciled with the wording used in tender documents.',
  // The absent / empty distinction is a real difference in the engine, so the
  // interface has to let the vendor express both rather than guessing.
  'profile.notDeclared': 'Nothing declared yet — criteria about these stay unresolved.',
  'profile.declaredNone': 'You have declared that you have none of these.',
  'profile.declareNone': 'I have none',
  'profile.undeclare': 'Undo — leave this unanswered',
  'profile.reassure':
    'An empty field is a question we will ask, not a mark against you.',

  // -- eligibility check -----------------------------------------------------
  'check.title': 'Eligibility check',
  'check.lead': 'Each requirement below was read from the tender, then compared with your profile by ordinary arithmetic. The working is shown for every one.',
  'check.assessedAs': 'Assessed as {name}',
  'check.recheck': 'Check again',
  'check.noProfileTitle': 'No company profile yet',
  'check.noProfileBody': 'Fill in what your company has, and every open tender can be checked against it.',
  'check.createProfile': 'Create a profile',
  'check.status.eligible': 'Meets every requirement we could read',
  'check.status.eligibleBody': 'No criterion extracted from this notice rules you out.',
  'check.status.blocked': 'One requirement rules you out',
  'check.status.blockedBody': 'A mandatory criterion is not met on the figures you entered.',
  'check.status.incomplete': 'Not established yet',
  'check.status.incompleteBody': 'Some criteria need a value you have not declared. Add it and the answer becomes definite.',
  'check.status.unrated': 'Nothing extracted from this notice yet',
  'check.status.unratedBody': 'No requirement has been read from this tender, so nothing has been judged either way. This is not a pass and not a failure.',
  'check.hardGate': 'Hard eligibility',
  'check.hardGate.pass': 'Passes',
  'check.hardGate.fail': 'Does not pass',
  'check.hardGate.pending': 'Not decided',
  'check.hardGate.pendingHint': 'A mandatory requirement is still waiting on a value from you.',
  'check.coverage': 'Criteria with a definite answer',
  'check.counts.satisfied': 'Met',
  'check.counts.failed': 'Not met',
  'check.counts.unknown': 'Waiting on you',
  'check.verdict.satisfied': 'Met',
  'check.verdict.failed': 'Not met',
  'check.verdict.unknown': 'Not established yet',
  'check.mandatory': 'Mandatory',
  'check.preference': 'Preference',
  'check.showOriginal': 'The tender’s own wording',
  'check.hideOriginal': 'Hide the original',
  'check.evidence': 'Quoted from the tender',
  'check.evidenceNone': 'No quote was recorded for this requirement — treat it with caution.',
  'check.source': 'Source',
  'check.missingTitle': 'To settle this we need:',
  'check.missing.scalar': '{label}',
  'check.missing.collection': '{label} — none declared',
  'check.missing.recordField': '{field}, on your {entity}',
  'check.goToProfile': 'Add it to your profile',
  'check.showWorking': 'Show the working',
  'check.hideWorking': 'Hide the working',
  'check.appliesTo.single': 'Applies to the bidder',
  'check.appliesTo.jv_combined': 'Applies to all joint-venture parties combined',
  'check.appliesTo.jv_each': 'Applies to each joint-venture party',
  'check.appliesTo.jv_at_least_one': 'Applies to at least one joint-venture party',
  'check.layer.L1': 'Read from the notice by rule',
  'check.layer.L2': 'Extracted from the notice',
  'check.layer.L3': 'Extracted from the tender document',
  'check.grounding.verified': 'Quote found in the source',
  'check.grounding.unchecked': 'Quote not verified yet',
  'check.emptyTitle': 'No requirements read from this notice',
  'check.emptyBody': 'Many notices state no qualification criteria at all. When that happens the tender document itself has to be read, and for this notice that has not been done.',

  // -- The vendor supplies the document we could not reach --------------------
  'check.supply.title': 'The tender documents',
  'check.supply.body':
    'This notice does not state its qualification criteria. They are in the tender document, and the notice does not link it — so nothing here has been read yet. The notice publishes a contact precisely so that a bidder can ask for that document.',
  'check.supply.bodyPartial':
    'These are the documents the criteria above were read from. If the borrower sent you one we do not have, drop it into its slot and the criteria are read again from it.',
  'check.supply.slot.tor': 'Terms of Reference',
  'check.supply.slot.torHint': 'The TOR or REOI document — what the assignment is and who may bid for it.',
  'check.supply.slot.rfp': 'RFP / bidding document',
  'check.supply.slot.rfpHint': 'The Request for Proposals or the bidding document, where the qualification section lives.',
  'check.supply.slot.other': 'Anything else',
  'check.supply.slot.otherHint': 'An addendum, a clarification, an annex — anything the borrower sent that states a condition.',
  'check.supply.slot.held': 'Held',
  'check.supply.slot.chars': '{count} characters read',
  'check.supply.slot.add': 'Choose a file',
  'check.supply.slot.replace': 'Add another',
  'check.supply.slot.link': 'Paste a link',
  'check.supply.slot.complete': 'Nothing more needed here.',
  'check.supply.origin.harvested': 'Mirrored from the notice',
  'check.supply.origin.supplied': 'Supplied by a vendor',
  'check.supply.contactTitle': 'Ask for the document',
  'check.supply.contact.name': 'Contact',
  'check.supply.contact.organization': 'Organization',
  'check.supply.contact.email': 'Email',
  'check.supply.contact.phone': 'Phone',
  'check.supply.contact.address': 'Address',
  'check.supply.contact.web': 'Website',
  'check.supply.urlPlaceholder': 'https://… — a Google Drive or Docs link works',
  'check.supply.submit': 'Read this document',
  'check.supply.working': 'Reading the document…',
  'check.supply.unreadable': 'The document arrived but could not be read: {problem}',
  'check.supply.foundNothing':
    'The document was read, but no qualification criterion was found in it.',
  'check.supply.found_one': '{count} requirement was read from the document.',
  'check.supply.found_other': '{count} requirements were read from the document.',
  'check.supply.privacy':
    'The document is stored and read here. Do not send anything you are not free to share.',
  'check.withheld_one': '{count} extracted requirement was withheld because its quote could not be found in the source.',
  'check.withheld_other': '{count} extracted requirements were withheld because their quotes could not be found in the source.',
  'check.openTender': 'Open the tender',

  // -- How ready the bid is ---------------------------------------------------
  'check.readFrom': 'Read from:',
  'check.readFrom.noneHeld': 'the notice text only',
  'check.readFrom.noTor': 'No Terms of Reference yet — the list below may be a fragment.',
  'check.readiness.title': 'How much of this tender you have established',
  'check.readiness.ceiling': 'up to {value} once the rest is settled',
  'check.readiness.counts': '{satisfied} of {total} criteria met',
  'check.readiness.open':
    'Only criteria you have established count towards this. The lighter part of the bar is what is still open.',
  'check.readiness.settled':
    'Every criterion read from this tender has an answer.',
  'check.readiness.blocked':
    'A mandatory criterion is not met. However high this figure goes, the bid is blocked until that changes.',
  'check.importance.high': 'Decides eligibility',
  'check.importance.medium': 'Required',
  'check.importance.low': 'Preference',

  'check.declare.question': 'Does your company meet this?',
  'check.declare.yes': 'We meet this',
  'check.declare.no': 'We do not',
  'check.declare.clear': 'Clear this answer',
  'check.declare.state.yes': 'We meet this',
  'check.declare.state.no': 'We do not meet this',
  'check.declare.state.unset': 'Not answered yet',
  'check.declare.byYou': 'Answered by you — not computed from your profile.',
  // -- The tender, open beside the criteria -----------------------------------
  'viewer.title': 'The tender text',
  'viewer.loading': 'Opening the tender…',
  'viewer.showInSource': 'Show me where',
  'viewer.hideInSource': 'Stop showing',
  'viewer.notLocated':
    'This criterion’s sentence could not be located in the text below. The quote on the card is still the verified one.',
  'viewer.problem.none':
    'There is no readable tender text to show for this notice yet.',
  'viewer.problem.noTextLayer':
    'The tender document is a scan with no text layer, so nothing in it can be pointed at.',
  'viewer.problem.fileMissing':
    'The tender document is not available on this server.',
  'viewer.problem.parserUnavailable':
    'This deployment cannot open the document for highlighting.',

  'check.disclaimer':
    'This compares the notice against what you entered. It is not a submission, and it does not replace reading the tender document.',
  'detail.checkEligibility': 'Check my eligibility',
  'layout.profile': 'My profile',

  // -- vendor accounts -------------------------------------------------------
  'auth.signInTitle': 'Sign in',
  'auth.signInBody':
    'Your profile and your eligibility checks are kept with your account, so you can pick up where you left off from any device.',
  'auth.registerTitle': 'Create an account',
  'auth.registerBody':
    'Tell us who you are once. After that every tender can be checked against what you have declared, and you only update it when your company changes.',
  'auth.company': 'Company name',
  'auth.country': 'Country',
  'auth.email': 'Email',
  'auth.password': 'Password',
  'auth.passwordHint': 'At least 8 characters, and not one of the common ones.',
  'auth.signInAction': 'Sign in',
  'auth.registerAction': 'Create the account',
  'auth.working': 'Please wait…',
  'auth.noAccount': 'No account yet?',
  'auth.haveAccount': 'Already have an account?',
  'auth.privacy':
    'What you declare is used only to answer your own eligibility checks.',
  'auth.backHome': 'Back to tenders',
  'auth.signOut': 'Sign out',
  'auth.signedInAs': 'Signed in as {email}',
  'auth.needAccountTitle': 'Sign in to check your eligibility',
  'auth.needAccountBody':
    'The check compares this tender against what your company has declared, so it needs an account. Reading what the tender asks for does not.',
  'auth.goToSignIn': 'Sign in or create an account',

  // -- API errors, keyed by the backend's error code -------------------------
  'error.network': 'Could not reach the server. Check your connection and try again.',
  'error.throttled': 'Too many requests. Please wait a moment and try again.',
  'error.http': 'Request failed with status {status}.',
  'error.unknown': 'Something went wrong.',
  'error.not_found': 'The requested record was not found.',
  'error.permission_denied': 'You do not have permission for this action.',
  'error.not_authenticated': 'Please sign in to continue.',
  'error.method_not_allowed': 'This request method is not supported here.',
  'error.invalid': 'The request was rejected as invalid.',
  'error.parse_error': 'The request body could not be read.',
  'error.service_unavailable': 'A dependent service is unavailable. Try again shortly.',
  'error.server': 'The server hit an unexpected error. Try again shortly.',

  // -- semantic search -------------------------------------------------------
  'layout.search': 'Search',
  'search.heading': 'Search the archive',
  'search.subtitle':
    'Ask in words, across notice texts and the bidding documents they link to. Every result opens the page it came from, with the passage marked.',
  'search.placeholder': 'e.g. advance payment guarantee, minimum annual turnover…',
  'search.submit': 'Search',
  'search.filter.category': 'Direction',
  'search.filter.allCategories': 'All directions',
  'search.noResults': 'Nothing found',
  'search.noResultsHint': 'Try fewer words, or a different direction.',
  'search.retrieval.vector': 'semantic match · {score}',
  'search.retrieval.fts': 'keyword match · {score}',
  'search.badge.page': 'Open page {page}',
  'search.badge.text': 'Open the source',
  'search.degraded.embeddings':
    'The semantic index is switched off in this deployment, so these are keyword matches over the notice texts — not a search of the mirrored documents.',
  'search.degraded.store':
    'The semantic index is unreachable, so these are keyword matches over the notice texts. Nothing is lost; the index can be rebuilt.',
  'search.degraded.keyword':
    'The semantic index returned nothing for this question, so these are keyword matches instead.',
  'search.citation.title': 'The source of this passage',
  'search.citation.notice': 'Tender',
  'search.citation.page': 'page',
  'search.citation.close': 'Close',
  'search.citation.loading': 'Opening the source…',
  'search.citation.fileUnavailable':
    'The document itself is not available on this server. The passage is still shown below.',
  'search.citation.sourceUnavailable':
    'The surrounding text could not be loaded. The passage is still shown below.',
  'search.citation.resize': 'Resize the source panel',
  'search.citation.resizeHint': 'Drag to resize · double-click to reset',

  // -- chat + semantic neighbours --------------------------------------------
  'chat.title': 'Ask the archive',
  'chat.close': 'Close',
  'chat.send': 'Ask',
  'chat.placeholder': 'Ask about requirements, deadlines, documents…',
  'chat.scopedToTender': 'Answering about this tender',
  'chat.scopedToArchive': 'Answering across every mirrored notice',
  'chat.intro':
    'Every sentence in an answer carries the passage it came from. Press a number to open that passage in the borrower’s own document.',
  'chat.example1': 'What financial requirements does this tender set?',
  'chat.example2': 'Is a joint venture allowed, and on what terms?',
  'chat.example3': 'Which documents must be submitted with the bid?',
  'chat.thinking': 'Reading the archive…',
  'chat.openSource': 'Open the source passage',
  'chat.nothingFound': 'Nothing in the archive answers this.',
  'chat.noAnswerButSources':
    'No answer could be written from these passages, but here they are.',
  'chat.keywordAnswer':
    'The semantic index did not answer this one, so these sentences are written over keyword matches — a weaker warrant.',
  'chat.dropped_one':
    '{count} sentence was removed because it cited nothing in the sources.',
  'chat.dropped_other':
    '{count} sentences were removed because they cited nothing in the sources.',
  'chat.degraded.model': 'No answering model is configured, so only the passages are shown.',
  'chat.degraded.failed': 'The answering model failed; the passages are unaffected.',
  'layout.chat': 'Chat',
  'chat.pageTitle': 'Ask the archive',
  'chat.openFull': 'Open in full view',
  'chat.newThread': 'New conversation',
  'chat.threads': 'Conversations',
  'chat.showThreads': 'Show the conversation list',
  'chat.hideThreads': 'Collapse the conversation list',
  'chat.citeRecord': 'This source is a row of our database, not a document. Opens the tender.',
  'chat.citeRecordList': 'This source is a row of our database, not a document. Opens the list of open tenders.',
  'chat.noThreads': 'No conversations yet. Ask something — it is kept here.',
  'chat.untitled': 'Untitled conversation',
  'chat.wholeArchive': 'Whole archive',
  'chat.rename': 'Rename',
  'chat.delete': 'Delete',
  'chat.welcomeTitle': 'What would you like to know?',
  'chat.loadingOlder': 'Loading earlier messages…',
  'chat.stage.retrieving': 'Turning the question into a vector…',
  'chat.stage.reading_one': 'Reading {count} source…',
  'chat.stage.reading_other': 'Reading {count} sources…',
  'chat.stage.claims_one': '{count} sentence written…',
  'chat.stage.claims_other': '{count} sentences written…',
  'chat.stage.writing': 'Writing the answer…',
  'chat.grounding':
    'Every sentence is tied to the passage it came from — click a number to read the original.',
  'chat.degraded.index': 'The semantic index is unavailable, so these are keyword matches.',

  'similar.openPassage': 'Open this sentence in the notice it came from',
  'similar.scoreHint':
    'Cosine similarity to this tender. Not checkable by reading — the quoted sentence is.',
} as const;

export type MessageKey = keyof typeof en;

type PluralCategory = 'zero' | 'one' | 'two' | 'few' | 'many' | 'other';

/** Base of every plural key, i.e. `filter.resultCount` from `…_other`. */
export type PluralBase = MessageKey extends infer K
  ? K extends `${infer Base}_other`
    ? Base
    : never
  : never;

/**
 * What `t()` accepts: any concrete key, or the base of a plural family — the
 * resolver appends the `Intl.PluralRules` category for the active language.
 */
export type TKey = MessageKey | PluralBase;

/**
 * Every language file must supply the full English key set. On top of that it
 * may add any plural category its language needs — Russian defines `_few` and
 * `_many` where English only has `_one`/`_other` — but nothing else, so a typo
 * is still rejected.
 */
export type Catalogue = Record<MessageKey, string> &
  Partial<Record<`${PluralBase}_${PluralCategory}`, string>>;

export default en satisfies Catalogue;
