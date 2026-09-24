import { NextResponse, type NextRequest } from 'next/server';

export function proxy(request: NextRequest) {
  const url = new URL(request.url);
  if (
    url.hostname === 'www.fxcss.com' ||
    (url.hostname === 'fxcss.com' && url.protocol === 'http:')
  ) {
    url.hostname = 'fxcss.com';
    url.protocol = 'https:';
    url.port = '';
    return NextResponse.redirect(url, 308);
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
    "frame-ancestors 'self'",
  ].join('; ');
  const headers = new Headers(request.headers);
  // Replace any client-supplied policy before the renderer reads its nonce.
  headers.set('Content-Security-Policy', csp);
  const response = NextResponse.next({ request: { headers } });
  response.headers.set('X-Content-Type-Options', 'nosniff');
  response.headers.set('Referrer-Policy', 'strict-origin-when-cross-origin');
  response.headers.set(
    'Permissions-Policy',
    'camera=(), microphone=(), geolocation=()',
  );
  response.headers.set('X-Frame-Options', 'SAMEORIGIN');
  response.headers.set('Content-Security-Policy', csp);
  response.headers.set('Cache-Control', 'private, no-store');
  return response;
}
export const config = { matcher: ['/:path*'] };
