// See ../index.html.
const videos = [...document.querySelectorAll('video')];
const out = document.getElementById('out');
for (const video of videos) {
  video.addEventListener('error', () => {
    document.body.className = 'failed';
    out.textContent += `${video.src}: error ${video.error && video.error.code}\n`;
  });
}
const timer = setInterval(() => {
  if (document.body.className === 'failed') return clearInterval(timer);
  if (videos.every(v => v.currentTime > 2)) {
    document.body.className = 'ok';
    clearInterval(timer);
  }
}, 250);
