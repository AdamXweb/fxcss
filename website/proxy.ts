import { NextResponse, type NextRequest } from 'next/server';

// Sent with every production response the Worker makes, redirects included.
// Static files are served before the Worker runs, so public/_headers gives
// them the same headers.
const SECURITY_HEADERS = {
  'Strict-Transport-Security': 'max-age=31536000; includeSubDomains',
  'X-Content-Type-Options': 'nosniff',
  'X-Frame-Options': 'DENY',
  'Permissions-Policy': 'camera=(), microphone=(), geolocation=()',
  'Referrer-Policy': 'strict-origin-when-cross-origin',
};

function withSecurityHeaders(response: NextResponse) {
  for (const [name, value] of Object.entries(SECURITY_HEADERS))
    response.headers.set(name, value);
  return response;
}

export function proxy(request: NextRequest) {
  const url = new URL(request.url);
  if (
    url.hostname === 'www.fxcss.com' ||
    (url.hostname === 'fxcss.com' && url.protocol === 'http:')
  ) {
    url.hostname = 'fxcss.com';
    url.protocol = 'https:';
    url.port = '';
    return withSecurityHeaders(NextResponse.redirect(url, 308));
  }
  if (process.env.NODE_ENV !== 'production') return NextResponse.next();
  const nonce = crypto.randomUUID().replaceAll('-', '');
  const csp = [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic' https://scripts.simpleanalyticscdn.com`,
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: https://queue.simpleanalyticscdn.com",
    "font-src 'self'",
    "connect-src 'self' https://queue.simpleanalyticscdn.com",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'none'",
    "frame-ancestors 'none'",
  ].join('; ');
  const headers = new Headers(request.headers);
  // Replace any client-supplied policy before the renderer reads its nonce.
  headers.set('Content-Security-Policy', csp);
  const response = withSecurityHeaders(
    NextResponse.next({ request: { headers } }),
  );
  response.headers.set('Content-Security-Policy', csp);
  response.headers.set('Cache-Control', 'private, no-store');
  return response;
}
export const config = { matcher: ['/:path*'] };
