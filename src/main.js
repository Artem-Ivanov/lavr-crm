import { createApp } from 'vue';
import App from './App.vue';
import './styles.css';

const mode = import.meta.env.VITE_DATA_MODE || (import.meta.env.MODE === 'demo' ? 'demo' : 'api');
let api;
if (mode === 'demo') {
  const { createDemoApi } = await import('./data/demo-api.js');
  api = createDemoApi();
} else if (mode === 'api') {
  const { createHttpApi } = await import('./data/http-api.js');
  api = createHttpApi({ baseUrl: import.meta.env.VITE_API_BASE_URL || '/api' });
} else {
  throw new Error('VITE_DATA_MODE должен быть api или demo.');
}
createApp(App, { api }).mount('#app');
