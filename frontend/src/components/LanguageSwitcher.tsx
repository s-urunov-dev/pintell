import { LANGUAGES, LANGUAGE_NAMES, LANGUAGE_SHORT, isLang, useI18n } from '../i18n';

/**
 * A native `<select>` on purpose: it is keyboard- and screen-reader-correct
 * everywhere, and on mobile it opens the platform picker. The visible text is
 * the short code so the header stays compact; each option names its language
 * in that language, which is the one label every reader can recognise.
 */
export default function LanguageSwitcher() {
  const { lang, setLang, t } = useI18n();

  return (
    <div className="lang-switcher">
      <span className="lang-current" aria-hidden="true">
        {LANGUAGE_SHORT[lang]}
      </span>
      <select
        aria-label={t('layout.languageAria')}
        value={lang}
        onChange={(event) => {
          if (isLang(event.target.value)) setLang(event.target.value);
        }}
      >
        {LANGUAGES.map((option) => (
          <option key={option} value={option} lang={option}>
            {LANGUAGE_NAMES[option]}
          </option>
        ))}
      </select>
    </div>
  );
}
