import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
import server


class ServerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory=tempfile.TemporaryDirectory()
        server.DATA=Path(self.directory.name)
        server.CURRENT=None
        server.PROJECTS.clear()
        self.sample=patch('server.sample_available',return_value=False)
        self.sample.start()
        app=web.Application(middlewares=[server.local_only]);app.add_routes(server.routes)
        app.router.add_static('/static/',server.ROOT/'static')
        self.client=TestClient(TestServer(app));await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close();self.sample.stop();self.directory.cleanup()

    async def test_clean_install_serves_ui(self):
        response=await self.client.get('/')
        self.assertEqual(response.status,200)
        self.assertIn('preview-resolution',await response.text())
        response=await self.client.get('/api/state');data=await response.json()
        self.assertEqual(data['status']['phase'],'empty')
        self.assertFalse(data['sample_available'])

    async def test_custom_port_origin(self):
        response=await self.client.post('/api/sample',json={},headers={'Origin':f'http://127.0.0.1:{self.client.port}'})
        self.assertEqual(response.status,404)

    async def test_foreign_origin_and_host_are_blocked(self):
        response=await self.client.post('/api/sample',json={},headers={'Origin':'https://example.com'})
        self.assertEqual(response.status,403)
        response=await self.client.get('/api/state',headers={'Host':'example.com'})
        self.assertEqual(response.status,403)


if __name__=='__main__':unittest.main()
