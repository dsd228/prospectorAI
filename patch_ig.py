# Ejecutar desde C:\Users\david\prospectorAI
# python patch_ig.py

with open('server.py', 'r', encoding='utf-8') as f:
    content = f.read()

old = """        req=urllib.request.Request('https://graph.facebook.com/v18.0/oauth/access_token',data=data,method='POST')
        r=urllib.request.urlopen(req)"""

new = """        req=urllib.request.Request('https://graph.facebook.com/v18.0/oauth/access_token',data=data,method='POST')
        print('DEBUG IG - App ID:', cfg.get('meta_app_id',''))
        print('DEBUG IG - Redirect URI:', f'{BASE_URL}/api/social/auth/instagram/callback')
        try:
            r=urllib.request.urlopen(req)
        except urllib.error.HTTPError as http_err:
            err_body = http_err.read().decode('utf-8')
            print('DEBUG IG - Facebook error:', http_err.code, err_body)
            return f'<html><body style="background:#080808;color:#f87171;font-family:sans-serif;padding:40px"><h2>Error Facebook:</h2><pre>{err_body}</pre></body></html>'"""

if old in content:
    content = content.replace(old, new)
    with open('server.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print('✓ Patch aplicado correctamente')
else:
    print('✗ No se encontró el bloque a reemplazar - revisar manualmente')