const http = require('http');
const fs = require('fs');
const path = require('path');

const PORT = process.env.PORT || 3000;
const BASE_DIR = __dirname;

const MIME_TYPES = {
  '.html': 'text/html; charset=UTF-8',
  '.css': 'text/css; charset=UTF-8',
  '.js': 'application/javascript; charset=UTF-8',
  '.json': 'application/json; charset=UTF-8',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.gif': 'image/gif',
  '.svg': 'image/svg+xml',
  '.ico': 'image/x-icon'
};

const server = http.createServer((req, res) => {
  // Security and CORS headers
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', '*');
  res.setHeader('X-Content-Type-Options', 'nosniff');
  res.setHeader('X-Frame-Options', 'SAMEORIGIN');
  res.setHeader('Referrer-Policy', 'strict-origin-when-cross-origin');

  if (req.method === 'OPTIONS') {
    res.writeHead(204);
    res.end();
    return;
  }

  let cleanUrl = req.url.split('?')[0];
  let decodedUrl;
  try {
    decodedUrl = decodeURIComponent(cleanUrl);
  } catch (e) {
    res.writeHead(400, { 'Content-Type': 'text/plain' });
    res.end('Bad Request');
    return;
  }
  let safePath = path.normalize(decodedUrl).replace(/^(\.\.[\/\\])+/, '');
  if (safePath === '/' || safePath === '\\' || safePath === '') {
    safePath = '/index.html';
  }

  const resolvedBase = path.resolve(BASE_DIR);
  const filePath = path.resolve(resolvedBase, '.' + (safePath.startsWith('/') || safePath.startsWith('\\') ? safePath : '/' + safePath));

  if (!filePath.startsWith(resolvedBase)) {
    res.writeHead(403, { 'Content-Type': 'text/plain' });
    res.end('Forbidden');
    return;
  }

  fs.stat(filePath, (err, stats) => {
    if (err || !stats.isFile()) {
      // Try appending .html
      const htmlPath = filePath + '.html';
      fs.stat(htmlPath, (errHtml, statsHtml) => {
        if (!errHtml && statsHtml.isFile()) {
          serveFile(htmlPath, '.html', res);
        } else {
          res.writeHead(404, { 'Content-Type': 'text/html; charset=UTF-8' });
          const notFoundPath = path.join(BASE_DIR, '404.html');
          if (fs.existsSync(notFoundPath)) {
            fs.createReadStream(notFoundPath).pipe(res);
          } else {
            res.end('<h1>404 Not Found</h1>');
          }
        }
      });
      return;
    }

    const ext = path.extname(filePath).toLowerCase();
    serveFile(filePath, ext, res);
  });
});

function serveFile(filePath, ext, res) {
  const contentType = MIME_TYPES[ext] || 'application/octet-stream';
  res.writeHead(200, { 'Content-Type': contentType });
  fs.createReadStream(filePath).pipe(res);
}

server.on('error', (err) => {
  if (err.code === 'EADDRINUSE') {
    console.log(`Port ${PORT} is already running a server.`);
  } else {
    console.error('Server error:', err);
  }
});

server.listen(PORT, () => {
  console.log(`\n======================================================`);
  console.log(` PeculiarAI Frontend Server running:`);
  console.log(`   ➜ Local:   http://localhost:${PORT}/login.html`);
  console.log(`   ➜ Home:    http://localhost:${PORT}/index.html`);
  console.log(`======================================================\n`);
});
