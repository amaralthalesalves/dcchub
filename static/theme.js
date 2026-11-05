// Aplica o tema salvo antes da pintura para evitar "flash" de cor.
try {
  const saved = localStorage.getItem('avalie-theme');
  if (saved === 'dark' || saved === 'light') document.documentElement.dataset.theme = saved;
} catch (_) { /* armazenamento indisponível */ }
