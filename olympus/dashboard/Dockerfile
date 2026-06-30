FROM node:22-alpine as builder
WORKDIR /app
COPY package*.json ./
RUN npm install
COPY . .

# --- CRITICAL SECTION START ---
# Accept build-time vars from docker-compose and expose them to the CRA build.
ARG REACT_APP_ZENOH_URL
ENV REACT_APP_ZENOH_URL=$REACT_APP_ZENOH_URL
ARG REACT_APP_VEHICLE_API_URL
ENV REACT_APP_VEHICLE_API_URL=$REACT_APP_VEHICLE_API_URL
# Without this the active instance defaulted to 'athena'; bake the selected one.
ARG REACT_APP_INSTANCE
ENV REACT_APP_INSTANCE=$REACT_APP_INSTANCE
# Cut build memory: sourcemaps are the heaviest part and OOM'd this box.
ENV GENERATE_SOURCEMAP=false
ENV NODE_OPTIONS=--max-old-space-size=2048
# --- CRITICAL SECTION END ---

RUN npm run build

FROM nginx:alpine
COPY --from=builder /app/build /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]