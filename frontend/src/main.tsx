import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';

import App from './App';
import { VendorAuthProvider } from './auth/VendorAuth';
import { I18nProvider } from './i18n';
import { migrateLegacyStorage } from './lib/storage';
import './styles/tokens.css';
import './styles/app.css';

// Before anything reads storage: the keys moved when the product was renamed,
// and a returning visitor's theme, language and draft profile live under the
// old ones. See `lib/storage.ts`.
migrateLegacyStorage();

const container = document.getElementById('root');
if (!container) {
  throw new Error('Root container #root is missing from index.html');
}

createRoot(container).render(
  <StrictMode>
    <I18nProvider>
      <BrowserRouter>
        {/* Inside the router: signing in navigates, and the provider is what
            knows where to. */}
        <VendorAuthProvider>
          <App />
        </VendorAuthProvider>
      </BrowserRouter>
    </I18nProvider>
  </StrictMode>,
);
