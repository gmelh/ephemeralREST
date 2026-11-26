import { render } from 'preact';
import { App } from './app';
import { startTheme } from './theme';

startTheme();
render(<App />, document.getElementById('app')!);
