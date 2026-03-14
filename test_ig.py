import sqlite3, urllib.request, urllib.parse 
c = sqlite3.connect('prospector.db') 
cfg = dict(c.execute('SELECT key,value FROM config').fetchall()) 
c.close() 
print('App ID:', cfg.get('meta_app_id','')) 
print('Secret:', cfg.get('meta_app_secret','')[:8]+'...') 
c = sqlite3.connect('prospector.db') 
