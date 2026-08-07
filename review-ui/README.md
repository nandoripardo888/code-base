# Review UI (React + Vite)

SPA moderna da página de review do code-harness.

## Desenvolvimento

```bash
cd review-ui
npm install
npm run build
```

O build grava `review.js` e `review.css` em `src/code_harness/review/static/`.
O `review.html` (shell com metas CSRF) é mantido pelo Python e não deve ser sobrescrito pelo Vite.

## Notas

- CSP do servidor exige assets locais (`script-src/style-src 'self'`), sem CDN.
- Após mudar UI, rode `npm run build` antes de abrir o portal.
