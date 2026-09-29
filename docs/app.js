const statusNode = document.getElementById('status');
const openLink = document.getElementById('open');
const retryButton = document.getElementById('retry');
const updatedNode = document.getElementById('updated');
let refreshTimer, refreshing = false;

async function refreshAddress() {
  if (refreshing) return;
  refreshing = true;
  clearTimeout(refreshTimer);
  if (openLink.hidden) statusNode.textContent = 'Checking the saved address…';
  try {
    const response = await fetch(`endpoint.json?t=${Date.now()}`, {cache: 'no-store', signal: AbortSignal.timeout(8000)});
    if (!response.ok) throw new Error('address_unavailable');
    const endpoint = await response.json();
    const date = new Date(endpoint.updated_at);
    updatedNode.textContent = Number.isFinite(date.getTime()) ? `Address updated ${date.toLocaleString()}` : '';
    if (endpoint.status !== 'online') {
      openLink.hidden = true;
      openLink.removeAttribute('href');
      statusNode.textContent = 'Waiting for the laptop. Checking again automatically.';
      retryButton.hidden = false;
      return;
    }
    const origin = new URL(endpoint.api_origin);
    if (origin.protocol !== 'https:' || origin.username || origin.password || origin.port || origin.pathname !== '/' || origin.search || origin.hash || !/^[a-z0-9-]+\.trycloudflare\.com$/.test(origin.hostname)) throw new Error('invalid_address');
    openLink.href = `${origin.origin}/try`;
    openLink.hidden = false;
    retryButton.hidden = true;
    statusNode.textContent = 'Open the voice interface.';
  } catch (_) {
    openLink.hidden = true;
    openLink.removeAttribute('href');
    statusNode.textContent = 'Waiting for a connection. Checking again automatically.';
    updatedNode.textContent = '';
    retryButton.hidden = false;
  } finally {
    refreshing = false;
    refreshTimer = setTimeout(() => {
      if (document.hidden) return;
      return refreshAddress();
    }, 10000);
  }
}
retryButton.addEventListener('click', refreshAddress);
window.addEventListener('online', refreshAddress);
document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshAddress(); });
refreshAddress();
