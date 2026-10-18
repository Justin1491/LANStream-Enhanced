document.querySelectorAll('.favorite-action').forEach(button => {
  button.addEventListener('click', async () => {
    const enabled = button.getAttribute('aria-pressed') !== 'true';
    const message = button.nextElementSibling;
    button.disabled = true;
    try {
      const response = await fetch('/api/favorites', {method: 'POST', headers: {
        'Content-Type': 'application/json', 'X-LANStream-CSRF': document.querySelector('meta[name="lanstream-csrf"]').content
      }, body: JSON.stringify({root_index: Number(button.dataset.root), rel: button.dataset.relative, kind: 'video', enabled})});
      if (!response.ok) throw Error('Unable to save favorite. Check access and try again.');
      button.setAttribute('aria-pressed', enabled); button.textContent = enabled ? '★' : '☆';
      message.textContent = '';
    } catch (error) { message.textContent = error.message; }
    finally { button.disabled = false; }
  });
});
