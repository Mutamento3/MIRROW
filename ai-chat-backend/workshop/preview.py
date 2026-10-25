"""Shared artifact preview policy: local form events, no outbound submission."""
SANDBOX = 'allow-scripts allow-forms'
HEADERS = {'X-Content-Type-Options':'nosniff', 'Cache-Control':'no-store',
    'Content-Security-Policy': "sandbox " + SANDBOX + "; default-src 'none'; img-src data:; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'none'; form-action 'none'; base-uri 'none'"}
