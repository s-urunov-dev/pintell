import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';

import App from './App';
import { I18nProvider } from './i18n';
import { migrateLegacyStorage } from './lib/storage';
import './styles/tokens.css';
import './styles/base.css';
import './styles/console.css';

// Before anything reads storage — see `lib/storage.ts`.
migrateLegacyStorage();

const container = document.getElementById('root');
if (!container) {
  throw new Error('Root container #root is missing from index.html');
}

createRoot(container).render(
  <StrictMode>
    <I18nProvider>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </I18nProvider>
  </StrictMode>,
);
