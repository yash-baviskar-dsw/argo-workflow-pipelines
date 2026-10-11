FROM nginx:1.27-alpine

COPY frontend/index.html /usr/share/nginx/html/index.html
COPY charts/demo/files/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
