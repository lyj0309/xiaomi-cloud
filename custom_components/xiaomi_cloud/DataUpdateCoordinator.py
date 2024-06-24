import asyncio
import json
import datetime
import time
import logging
import re
import base64
import hashlib
import math
from urllib import parse
import aiohttp
import async_timeout
from aiohttp.client_exceptions import ClientConnectorError
from homeassistant.core import Config, HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.components.device_tracker import (
    ATTR_BATTERY,
    DOMAIN as DEVICE_TRACKER,
)

from .const import (
    DOMAIN,
)
_LOGGER = logging.getLogger(__name__)

class XiaomiCloudDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching XiaomiCloud data API."""
    def __init__(self, hass, user, password, scan_interval, coordinate_type):
        """Initialize."""
        self._username = user
        self._password = password
        self._headers = {}
        self._cookies = {}
        self._device_info = {}
        self._serviceLoginAuth2_json = {}
        self._sign = None
        self._scan_interval = scan_interval
        self._coordinate_type = coordinate_type
        self.service_data = None
        self.userId = None
        self.login_result = False
        self.service = None

        update_interval = (
            datetime.timedelta(minutes=self._scan_interval)
        )
        _LOGGER.debug("Data will be update every %s", update_interval)

        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=update_interval)

    async def _get_sign(self, session):
        url = 'https://account.xiaomi.com/pass/serviceLogin?sid%3Di.mi.com&sid=i.mi.com&_locale=zh_CN&_snsNone=true'
        pattern = re.compile(r'_sign=(.*?)&')
        _LOGGER.debug("_get_sign",self._headers)
        try:
            with async_timeout.timeout(15):
                r = await session.get(url, headers=self._headers)
            self._cookies['pass_trace'] = r.history[0].headers.getall('Set-Cookie')[2].split(";")[0].split("=")[1]
            _LOGGER.debug("--2---%s",parse.unquote(pattern.findall(r.history[0].headers.getall('Location')[0])[0]))
            self._sign = parse.unquote(pattern.findall(r.history[0].headers.getall('Location')[0])[0])
            return True
        except BaseException as e:
            _LOGGER.warning(e.args[0])
            return False

    async def _serviceLoginAuth2(self, session, captCode=None):
        url = 'https://account.xiaomi.com/pass/serviceLoginAuth2'
        self._headers['Content-Type'] = 'application/x-www-form-urlencoded'
        self._headers['Accept'] = '*/*'
        self._headers['Origin'] = 'https://account.xiaomi.com'
        self._headers['Referer'] = 'https://account.xiaomi.com/pass/serviceLogin?sid%3Di.mi.com&sid=i.mi.com&_locale=zh_CN&_snsNone=true'
        self._headers['Cookie'] = 'pass_trace={};'.format(self._cookies['pass_trace'])

        auth_post_data = {'_json': 'true',
                          '_sign': self._sign,
                          'callback': 'https://i.mi.com/sts',
                          'hash': hashlib.md5(self._password.encode('utf-8')).hexdigest().upper(),
                          'qs': '%3Fsid%253Di.mi.com%26sid%3Di.mi.com%26_locale%3Dzh_CN%26_snsNone%3Dtrue',
                          'serviceParam': '{"checkSafePhone":false}',
                          'sid': 'i.mi.com',
                          'user': self._username}
        try:
            if captCode != None:
                url = 'https://account.xiaomi.com/pass/serviceLoginAuth2?_dc={}'.format(
                    int(round(time.time() * 1000)))
                auth_post_data['captCode'] = captCode
                self._headers['Cookie'] = self._headers['Cookie'] + \
                                          '; ick={}'.format(self._cookies['ick'])
            with async_timeout.timeout(15):
                r = await session.post(url, headers=self._headers, data=auth_post_data, cookies=self._cookies)
            self._cookies['pwdToken'] = r.cookies.get('passToken').value
            self._serviceLoginAuth2_json = json.loads((await r.text())[11:])
            return True
        except BaseException as e:
            _LOGGER.warning(e.args[0])
            return False

    async def _login_miai(self, session):
        serviceToken = "nonce={}&{}".format(
            self._serviceLoginAuth2_json['nonce'], self._serviceLoginAuth2_json['ssecurity'])
        serviceToken_sha1 = hashlib.sha1(serviceToken.encode('utf-8')).digest()
        base64_serviceToken = base64.b64encode(serviceToken_sha1)
        loginmiai_header = {'User-Agent': 'MISoundBox/1.4.0,iosPassportSDK/iOS-3.2.7 iOS/11.2.5',
                            'Accept-Language': 'zh-cn', 'Connection': 'keep-alive'}
        url = self._serviceLoginAuth2_json['location'] + \
              "&clientSign=" + parse.quote(base64_serviceToken.decode())
        try:
            with async_timeout.timeout(15):
                r = await session.get(url, headers=loginmiai_header)
            if r.status == 200:
                self._Service_Token = r.cookies.get('serviceToken').value
                self.userId = r.cookies.get('userId').value
                _LOGGER.debug("_login_miai",url,"userid",self.userId)

                return True
            else:
                return False
        except BaseException as e:
            _LOGGER.warning(e.args[0])
            return False

    async def _get_device_info(self, session):
        url = 'https://i.mi.com/find/device/full/status?ts={}'.format(
            int(round(time.time() * 1000)))
        get_device_list_header = {'Cookie': 'userId={};serviceToken={}'.format(
            self.userId, self._Service_Token)}
        try:
            with async_timeout.timeout(15):
                r = await session.get(url, headers=get_device_list_header)
            if r.status == 200:
                _LOGGER.debug('get_device info',json.loads(await r.text())['data']['devices'])
                data = json.loads(await r.text())['data']['devices']

                self._device_info = data
                return True
            else:
                return False
        except BaseException as e:
            _LOGGER.warning(e.args[0])
            return False  
    
    async def _send_find_device_command(self, session:aiohttp.ClientSession):
        flag = True
        for vin in self._device_info:
            imei = vin["imei"]  
            url = 'https://i.mi.com/find/device/{}/location'.format(
                imei)
            _send_find_device_command_header = {
                'Cookie': 'userId={};serviceToken={}'.format(self.userId, self._Service_Token)}
            data = {'userId': self.userId, 'imei': imei,
                    'auto': 'false', 'channel': 'web', 'serviceToken': self._Service_Token}
            try:
                with async_timeout.timeout(15):
                    r = await session.post(url, headers=_send_find_device_command_header, data=data)
                _LOGGER.info("find_device res: %s", await r.json())
                if r.status == 200:
                    flag = True
                else:
                    flag = False
                    self.login_result = False
            except BaseException as e:
                _LOGGER.warning(e.args[0])
                self.login_result = False
                flag = False
        return flag
    
    async def _send_noise_command(self, session:aiohttp.ClientSession):
        flag = True
        imei = self.service_data['imei']  
        url = 'https://i.mi.com/find/device/{}/noise'.format(
            imei)
        _send_noise_command_header = {
            'Cookie': 'userId={};serviceToken={}'.format(self.userId, self._Service_Token)}
        data = {'userId': self.userId, 'imei': imei,
                'auto': 'false', 'channel': 'web', 'serviceToken': self._Service_Token}
        try:
            with async_timeout.timeout(15):
                r = await session.post(url, headers=_send_noise_command_header, data=data)
            _LOGGER.debug("noise res: %s", await r.json())
            if r.status == 200:
                flag = True
                self.service = None
                self.service_data = None
            else:
                flag = False
                self.login_result = False
        except BaseException as e:
            _LOGGER.warning(e.args[0])
            self.login_result = False
            flag = False
        return flag

    async def _send_lost_command(self, session:aiohttp.ClientSession):
        flag = True
        imei = self.service_data['imei']  
        content = self.service_data['content']  
        phone = self.service_data['phone']  
        message = {"content":content, "phone": phone}
        onlinenotify = self.service_data['onlinenotify']
        url = 'https://i.mi.com/find/device/{}/lost'.format(
            imei)
        _send_lost_command_header = {
            'Cookie': 'userId={};serviceToken={}'.format(self.userId, self._Service_Token)}
        data = {'userId': self.userId, 'imei': imei,
                'deleteCard': 'false', 'channel': 'web', 'serviceToken': self._Service_Token, 'onlineNotify': onlinenotify, 'message':json.dumps(message)}
        try:
            with async_timeout.timeout(15):
                r = await session.post(url, headers=_send_lost_command_header, data=data)
            _LOGGER.debug("lost res: %s", await r.json())    
            if r.status == 200:
                flag = True
                self.service = None
                self.service_data = None
            else:
                flag = False
                self.login_result = False
        except BaseException as e:
            _LOGGER.warning(e.args[0])
            self.login_result = False
            flag = False
        return flag

    async def _send_clipboard_command(self, session:aiohttp.ClientSession):
        flag = True
        text = self.service_data['text']  
        url = 'https://i.mi.com/clipboard/lite/text'
        _send_clipboard_command_header = {
            'Cookie': 'userId={};serviceToken={}'.format(self.userId, self._Service_Token)}
        data = {'text': text, 'serviceToken': self._Service_Token}
        try:
            with async_timeout.timeout(15):
                r = await session.post(url, headers=_send_clipboard_command_header, data=data)
            _LOGGER.debug("clipboard res: %s", await r.json())    
            if r.status == 200:
                flag = True
                self.service = None
                self.service_data = None
            else:
                flag = False
                self.login_result = False
        except BaseException as e:
            _LOGGER.warning(e.args[0])
            self.login_result = False
            flag = False
        return flag
  
    async def _send_command(self, data):
        self.service_data = data['data']
        self.service = data['service']
        await self.async_refresh()

    async def _get_device_location(self, session:aiohttp.ClientSession):
        devices_info = []
        for vin in self._device_info:
            imei = vin["imei"] 
            model = vin["model"] 
            version = vin["version"]
            url = 'https://i.mi.com/find/device/status?ts={}&fid={}'.format(
                int(round(time.time() * 1000)), imei)
            _send_find_device_command_header = {
                'Cookie': 'userId={};serviceToken={}'.format(self.userId, self._Service_Token)}
            try:
                with async_timeout.timeout(15):
                    r = await session.get(url, headers=_send_find_device_command_header)
                if r.status == 200:
                    _LOGGER.info(f"get_device_location_data,user:{self.userId},imei:{imei},model:{model} ,res: {json.loads(await r.text())}")

                    if "receipt" in json.loads(await r.text())['data']['location']:
                        gpsInfoTransformed = json.loads(await r.text())['data']['location']['receipt']['gpsInfoTransformed']

                        device_info = {}
                        location_info_json = {}
                        if self._coordinate_type == "original":
                            location_info_json = json.loads(await r.text())['data']['location']['receipt']['gpsInfo']
                            # wgs84 = self.GCJ2WGS(gpsInfoExtra[0]['longitude'],gpsInfoExtra[0]['latitude'])
                            # _LOGGER.debug("get_device_location_data_wgs84: %s", wgs84)
                            # location_info_json = {
                            #     "accuracy":int(gpsInfoExtra[0]['accuracy']),
                            #     "coordinateType":'wgs84',
                            #     "latitude":wgs84[1],
                            #     "longitude":wgs84[0],
                            # }                            
                        else:
                            location_info_json = next((item for item in gpsInfoTransformed if item.get('coordinateType') == self._coordinate_type), None)

                        device_info["device_lat"] = location_info_json['latitude']
                        device_info["device_accuracy"] = int(location_info_json['accuracy'])
                        device_info["device_lon"] = location_info_json['longitude']
                        device_info["coordinate_type"] = location_info_json['coordinateType']

                        device_info["device_power"] = json.loads(
                            await r.text())['data']['location']['receipt'].get('powerLevel',0)
                        device_info["device_phone"] = json.loads(
                            await r.text())['data']['location']['receipt'].get('phone',0)
                        timeArray = time.localtime(int(json.loads(
                            await r.text())['data']['location']['receipt']['infoTime']) / 1000)
                        device_info["device_location_update_time"] = time.strftime("%Y-%m-%d %H:%M:%S", timeArray)
                        device_info["imei"] = imei
                        device_info["model"] = model
                        device_info["version"] = version
                        devices_info.append(device_info)
                    else:
                        self.login_result = False
                else:
                    self.login_result = False
            except BaseException as e:
                self.login_result = False
                _LOGGER.error(e)
        return devices_info
    def GCJ2WGS(self,lon,lat):
        a = 6378245.0 # 克拉索夫斯基椭球参数长半轴a
        ee = 0.00669342162296594323 #克拉索夫斯基椭球参数第一偏心率平方
        PI = 3.14159265358979324 # 圆周率

        x = lon - 105.0
        y = lat - 35.0

        dLon = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * math.sqrt(abs(x));
        dLon += (20.0 * math.sin(6.0 * x * PI) + 20.0 * math.sin(2.0 * x * PI)) * 2.0 / 3.0;
        dLon += (20.0 * math.sin(x * PI) + 40.0 * math.sin(x / 3.0 * PI)) * 2.0 / 3.0;
        dLon += (150.0 * math.sin(x / 12.0 * PI) + 300.0 * math.sin(x / 30.0 * PI)) * 2.0 / 3.0;

        dLat = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * math.sqrt(abs(x));
        dLat += (20.0 * math.sin(6.0 * x * PI) + 20.0 * math.sin(2.0 * x * PI)) * 2.0 / 3.0;
        dLat += (20.0 * math.sin(y * PI) + 40.0 * math.sin(y / 3.0 * PI)) * 2.0 / 3.0;
        dLat += (160.0 * math.sin(y / 12.0 * PI) + 320 * math.sin(y * PI / 30.0)) * 2.0 / 3.0;
        radLat = lat / 180.0 * PI
        magic = math.sin(radLat)
        magic = 1 - ee * magic * magic
        sqrtMagic = math.sqrt(magic)
        dLat = (dLat * 180.0) / ((a * (1 - ee)) / (magic * sqrtMagic) * PI);
        dLon = (dLon * 180.0) / (a / sqrtMagic * math.cos(radLat) * PI);
        wgsLon = lon - dLon
        wgsLat = lat - dLat
        return [wgsLon,wgsLat]

    async def _async_update_data(self):
        """Update data via library."""
        _LOGGER.debug("service: %s", self.service)
        try:
            session = async_get_clientsession(self.hass)
            if self.login_result is True:
                if self.service == "noise":
                    tmp = await self._send_noise_command(session)
                elif self.service == 'lost':
                    tmp = await self._send_lost_command(session)
                elif self.service == 'clipboard':
                    tmp = await self._send_clipboard_command(session)
                elif self._scan_interval>0:
                    tmp = await self._send_find_device_command(session)
                if tmp is True:
                    await asyncio.sleep(15)
                    tmp = await self._get_device_location(session)
                    if not tmp:
                        _LOGGER.info("_get_device_location0 Failed")
                    else:
                        _LOGGER.info("_get_device_location0 succeed")
                else:
                    _LOGGER.info("send_command Failed")
            else:
                if self._scan_interval>0:
                    session.cookie_jar.clear()
                    tmp = await self._get_sign(session)
                    if not tmp:
                        _LOGGER.warning("get_sign Failed")
                    else:
                        tmp = await self._serviceLoginAuth2(session)
                        if not tmp:
                            _LOGGER.warning('Request Login_url Failed')
                        else:
                            if self._serviceLoginAuth2_json['code'] == 0:
                                # logon success,run self._login_miai()
                                tmp = await self._login_miai(session)
                                if not tmp:
                                    _LOGGER.warning('login Mi Cloud Failed')
                                else:
                                    tmp = await self._get_device_info(session)
                                    if not tmp:
                                        _LOGGER.warning('get_device info Failed')
                                    else:
                                        _LOGGER.info("get_device info succeed")
                                        self.login_result = True
                                        if self.service == "noise":
                                            tmp = await self._send_noise_command(session)
                                        elif self.service == 'lost':
                                            tmp = await self._send_lost_command(session)
                                        elif self.service == 'clipboard':
                                            tmp = await self._send_clipboard_command(session)
                                        else:
                                            tmp = await self._send_find_device_command(session)
                                        if tmp is True:
                                            await asyncio.sleep(5)
                                            tmp = await self._get_device_location(session)
                                            if not tmp:
                                                _LOGGER.info("_get_device_location1 Failed")
                                            else:
                                                _LOGGER.info("_get_device_location1 succeed")
                                        else:
                                            _LOGGER.info("send_command Failed")

        except (
            ClientConnectorError
        ) as error:
            raise UpdateFailed(error)
        return tmp
