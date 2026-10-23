const http = require("http");

// App Service tells the app which port to listen on through PORT.
const port = process.env.PORT || 8080;
const environment = process.env.ENVIRONMENT || "local";

const server = http.createServer((req, res) => {
  res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
  res.end(`<!doctype html>
<html>
  <head><title>Web App (${environment})</title></head>
  <body style="font-family: sans-serif; margin: 3rem;">
    <h1>Hello from Azure App Service</h1>
    <p>Environment: <strong>${environment}</strong></p>
    <p>Deployed with Terraform and the Azure CLI.</p>
  </body>
</html>`);
});

server.listen(port, () => {
  console.log(`Listening on port ${port} (${environment})`);
});