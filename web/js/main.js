// The page's one module (web/index.html): it loads every part of KINO, in the
// order the page used to list them, then KINO starts. Each part imports what it
// uses from the others and registers what the markup may run
// (web/js/actions.js).
import './actions.js';
import './html.js';
import './series-playback.js';
import './spoilers.js';
import './anime-journey.js';
import './app-core.js';
import './app-home.js';
import './app-lists.js';
import './app-letterboxd.js';
import './app-tabs.js';
import './app-cloud-settings.js';
import './app-social.js';
import './app-fiche.js';
import './app-sources.js';
import './app-player.js';
import './ratings.js';
import './anime-home.js';
import './p2p-addons.js';
import './manga.js';
import { startKino } from './app-start.js';
import './stats.js';
import './playback-help.js';
import './native-player.js';

startKino();
