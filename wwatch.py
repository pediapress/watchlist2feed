#!/usr/bin/env python
DOCUMENTATION = """
This service offers to get Mediawiki watchlists as ATOM feeds

HTTP Authentication is used to query for your user:pwd of your wiki account,
which is then used to access the MediaWiki API.
No personal information is logged nor persisted.

(c) PediaPress, 2009

"""

import sys
import cgi
import urllib,urllib2,cookielib,httplib
try:
    import json
except:
    import simplejson as json

import datetime
import time
import BaseHTTPServer
import re
import base64
user_agent="wwatch.py v0.1"


class LoginFailedException(Exception):
        pass


def callapi(cookie, **data):
    data['format'] = 'json'
    opener=urllib2.build_opener(urllib2.HTTPCookieProcessor(cookie))
    req=urllib2.Request(cookie.apiURL,urllib.urlencode(data.items()))
    #print req.get_full_url()
    #print req.get_method()
    req.add_header("User-Agent",user_agent)
    return json.load(opener.open(req))



def wikiauth(uname,passwd,domain):
    #Get login response
    cookie=cookielib.CookieJar()
    cookie.wdomain = domain
    cookie.wuser = uname
    cookie.apiURL = 'http://%s/w/api.php' % domain
    cookie.indexURL = 'http://%s/w/index.php' % domain

    d=callapi(cookie, action="login",
              lgname=uname,
              lgpassword = passwd)
    r = d['login']['result']
    if not 'Success' == r:
        raise LoginFailedException("Authentication denied: " + r)
    return cookie

def get_feed(cookie, limit=5):

    d = callapi(cookie, action= 'query',
                list='watchlist',
                wlallrev ='',
                wlprop = 'ids|title|timestamp|user|comment|flags|sizes',
                wllimit=limit)
    feed =  d['query']['watchlist']
    #for x in feed: print x +"\n"
    return gen_output(feed, cookie)



def gen_output(feed, cookie):

    entry_template = \
"""<entry>
    <title>%(title)s</title>
    <link href="%(href_page)s"/>
    <id>%(uuid)s</id>
    <updated>%(updated)s</updated>
        <content type="xhtml">
          <div xmlns="http://www.w3.org/1999/xhtml">
                %(summary)s
           </div>
        </content>
    </entry>
"""

    change_template = \
"""
   <li>
   %(time)s
   <strong>%(flag)s</strong>
   (<a href="%(href_diff)s">diff</a>)
   (%(size)s)
   <a href="%(href_user)s">%(user)s</a>
   (<a href="%(href_user_talk)s">talk</a>)
   (<a href="%(href_user_contribs)s">contribs</a>)
    (<i>%(comment)s</i>)
   </li>"""

    def iurl(**kargs):
        for k,v in kargs.items():
            try:
                kargs[k] = v.encode('utf8')
            except AttributeError:
                pass
        return cookie.indexURL + "?" + urllib.urlencode(kargs)

    def get_date(ts):
        # 2009-09-18T22:04:52Z, python datetime documentation sucked, for me
        return ts[:10], ts[11:16]

    # prepare sorted list of all changed pages
    titles = []
    for x in feed:
        if x['title'] not in titles:
            titles.append(x['title'])

    entries = []

    for title in titles: # with all changed pages
        last_day = None
        e = dict(title = title,
                 summary = u"",
                 updated = None,
                 uuid = None,
                 href_page = None)
        for x in feed: # with all changes ...
            if title != x['title']: # ... of this page
                continue
            if not e['updated']: # latest changeset
                e['updated'] = x['timestamp']
                e['uuid'] =  '%s %s' %(title, x['revid'])
                e['href_page'] = iurl(title=title)
            x['size'] = str(x['newlen'] - x['oldlen'])
            if x['size'][0] != '-':
                x['size'] = '+'+x['size']
            x['flag'] = x.get('flag',u'')
            x['href_diff'] = iurl(title=title,
                                  oldid=x["revid"],
                                  diff = 'prev')

            x['href_user'] = iurl(title='User:%s'%x['user'])
            x['href_user_talk'] = iurl(title='User_talk:%s'%x['user'])
            x['href_user_contribs'] = iurl(title='Special:Contributions/%s'%x['user'])
            for f in ('minor','bot','new'):
                if f in x:
                    x['flag'] = f
            day, x['time'] = get_date(x['timestamp'])
            if day != last_day:
                if last_day is not None:
                    e['summary'] += "</ul>"
                e['summary'] += "<h2>%s</h2><ul>" % day
                last_day = day
            e['summary'] += change_template % x
        e['summary'] += "</ul>"
        entries.append( entry_template % e)

    href_css = "http://en.wikipedia.org/skins-1.5/common/common.css"
    title = "Watchlist for %s" % cookie.wdomain
    href_self = "http://%s/wiki/Special:Watchlist" %cookie.wdomain #FIXME

    updated = datetime.datetime.isoformat(datetime.datetime.utcnow().replace(microsecond=0)) + ""


    answer = \
'''<?xml version="1.0"?>
<?xml-stylesheet type="text/css" href="%(href_css)s"?>
<feed xmlns="http://www.w3.org/2005/Atom" xml:lang="en">
                <id>urn:%(href_self)s</id>
                <title>%(title)s</title>
                <updated>%(updated)s</updated>
           <link href="%(href_self)s" />

<author><name>your watchlist</name></author>
 %(entries)s
        </feed>
''' % dict( href_css = href_css,
            href_self= href_self,
            title=title,
            updated = updated,
            entries = "\n".join(entries) )
    return answer




class MyHandler(BaseHTTPServer.BaseHTTPRequestHandler):

    def send_401(self, error, domain):
        self.send_response(401, 'UNAUTHORIZED')
        self.send_header('WWW-Authenticate', 'Basic realm="Please provide your credentials for %s"' % domain)
        self.send_header('Content-type','text/html')
        self.end_headers()
        self.wfile.write('<html><body><h1>Error: Authentication needed</h1>%s</body></html>'%error)


    def send_500(self, error, domain):
        self.send_response(500, 'Server Error')
        self.send_header('Content-type','text/html')
        self.end_headers()
        self.wfile.write('<html><body><h1>Error: A server error happended, when accessing "%s"</h1>%s</body></html>'%(domain,error))




    def _authenticate(self, domain):
        error = ""
        m = 'Authorization: Basic '
        a = [x[len(m):].strip() for x in str(self.headers).split("\n")
             if x.startswith(m)]
        if domain and a:
            user, pwd = base64.b64decode(a[0]).split(':')
            try:
                return wikiauth(user, pwd, domain)
            except LoginFailedException,inst:
                sys.stderr.write("Login failed: %s\n"%inst.message)
                error = inst.message
            except Exception, inst:
                sys.stderr.write("Login failed: %s\n"%inst.message)
                error = inst.message
                return self.send_500(error,domain)
        return self.send_401(error, domain)

    def source(s):
        s.send_response(200)
        s.send_header("Content-type", "text/plain")
        s.end_headers()
        s.wfile.write(open(sys.argv[0]).read())


    def documentation(s):
        s.do_HEAD()
        out = '''
        <html><body>
<h1>MediaWiki Watchlists as Atom-Feed</h1>
Wiki domain (w/o http):
<input type="text" id="domain" value='en.wikipedia.org'/>
<input type='submit' onclick="javascript:window.location='/'+getElementById('domain').value;" label='go'/>
<pre>%s</pre>

You can download the <a href='/source'>source code</a> of this software and run it on your own machine.

</body></html>''' % DOCUMENTATION
        s.wfile.write(out)


    def do_HEAD(s):
        s.send_response(200)
        s.send_header("Content-type", "text/html")
        s.end_headers()

    def do_GET(s):
        """Respond to a GET request."""
        #print str(s.headers)
        #print s.path
        domain = s.path.replace('/','').replace('index.xml','')
        if not domain:
            return s.documentation()
        if domain == 'source':
            return s.source()
        cookie = s._authenticate(domain)
        if not cookie:
            return

        data = get_feed(cookie, limit=500)
        s.send_response(200)
        s.send_header("Content-type", "application/atom+xml")
        s.end_headers()
        s.wfile.write(data.encode('utf8'))


def test():
    cookie = wikiauth('He!ko', "", 'en.wikipedia.org')
    print get_feed(cookie, limit=10)
    sys.exit(0)

def start_server():
    sav = sys.argv
    HOST_NAME = len(sav) >= 2 and sav[1] or ''
    PORT_NUMBER = len(sav) == 3 and int(sav[2]) or 9000
    server_class = BaseHTTPServer.HTTPServer
    httpd = server_class((HOST_NAME, PORT_NUMBER), MyHandler)
    print time.asctime(), "Server Starts - %s:%s" % (HOST_NAME, PORT_NUMBER)
    try:
        httpd.serve_forever()
    except Exception:
        pass
    httpd.server_close()
    raise Exception


if __name__ == '__main__':
    #test() # use for cmd line testing
    while True:
        try:
            start_server()
        except KeyboardInterrupt:
            break




