"""Reject Python networking during local chat reading and export."""
import sys

def enforce_offline():
    def audit(event,args):
        if event in ('socket.connect','socket.connect_ex','socket.getaddrinfo','socket.sendto','socket.bind','urllib.Request'):
            raise PermissionError('拾聊离线模式禁止网络请求：'+event)
    sys.addaudithook(audit)
