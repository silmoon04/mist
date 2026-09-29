const statusNode = document.getElementById('status');
const openLink = document.getElementById('open');
const retryButton = document.getElementById('retry');
const updatedNode = document.getElementById('updated');

async function refreshAddress() {
  openLink.hidden = true;
  openLink.removeAttribute('href');
  retryButton.hidden = true;
  statusNode.textContent = 'Checking the saved address…';
  try {
    const response = await fetch(`endpoint.json?t=${Date.now()}`, {cache: 'no-store'});
    if (!response.ok) throw new Error('address_unavailable');
    const endpoint = await response.json();
    const date = new Date(endpoint.updated_at);
    updatedNode.textContent = Number.isFinite(date.getTime()) ? `Address updated ${date.toLocaleString()}` : '';
    if (endpoint.status !== 'online') {
      statusNode.textContent = 'The laptop service is stopped.';
      retryButton.hidden = false;
      return;
    }
    const origin = new URL(endpoint.api_origin);
    if (origin.protocol !== 'https:' || origin.username || origin.password || origin.port || origin.pathname !== '/' || origin.search || origin.hash || !/^[a-z0-9-]+\.trycloudflare\.com$/.test(origin.hostname)) throw new Error('invalid_address');
    openLink.href = `${origin.origin}/try`;
    openLink.hidden = false;
    statusNode.textContent = 'Open the voice interface.';
  } catch (_) {
    statusNode.textContent = 'The address is not available yet.';
    updatedNode.textContent = '';
    retryButton.hidden = false;
  }
}
retryButton.addEventListener('click', refreshAddress);
refreshAddress();
