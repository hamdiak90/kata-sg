import http from 'k6/http';
import { check, sleep } from 'k6';

const API = __ENV.API_URL;

export const options = {
  stages: [
    { duration: '30s', target: 20 },
    { duration: '1m',  target: 50 },
    { duration: '30s', target: 0 },
  ],
  thresholds: {
    http_req_failed:   ['rate<0.01'],
    http_req_duration: ['p(95)<500'],
  },
};

export default function () {
  if (Math.random() < 0.8) {
    const res = http.get(`${API}/api/items`);
    check(res, { 'lecture 200': (r) => r.status === 200 });
  } else {
    const body = JSON.stringify({ name: `k6-${__VU}-${__ITER}` });
    const res = http.post(`${API}/api/items`, body, {
      headers: { 'Content-Type': 'application/json' },
    });
    check(res, { 'ecriture 201': (r) => r.status === 201 });
  }
  sleep(1);
}
