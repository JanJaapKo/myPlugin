# Basic Python Plugin Example
#
# Author: Jan-Jaap Kostelijk
#
"""
<plugin key="DysonPureLink" name="Dyson Pure Link" author="jan-jaap kostelijk" version="1.1.18" >
    <description>
        <h2>Dyson Pure Link plugin</h2><br/>
        Connects to Dyson Pure Link device, CoolLink 475<br/>
		Dikke vette yoyo!
    </description>
    <params>
		<param field="Address" label="IP Address" width="200px" required="true" default="192.168.1.15"/>
		<param field="Port" label="Port" width="30px" required="true" default="1883"/>
		<param field="Mode1" label="Dyson type (Pure Cool only at this moment)">
            <options>
                <option label="455" value="455"/>
                <option label="465" value="465"/>
                <option label="475" value="475" default="true"/>
            </options>
        </param>
		<param field="Mode2" label="Dyson Serial No." default="NN2-EU-JEA3830A" required="true"/>
		<param field="Password" label="Dyson Password (see machine)" required="true" password="true"/>
		<param field="Mode5" label="Update count (10 sec)" default="3" required="true"/>
		<param field="Mode4" label="Debug" width="75px">
            <options>
                <option label="True" value="Debug" default="true"/>
                <option label="False" value="Normal"/>
            </options>
        </param>
    </params>
</plugin>
"""

import Domoticz
import base64, json, hashlib, os, time
import paho.mqtt.client as mqtt
from queue import Queue, Empty

from value_types import CONNECTION_STATE, DISCONNECTION_STATE, FanMode, StandbyMonitoring, ConnectionError, DisconnectionError, SensorsData, StateData
#from dyson_pure_link_device import DysonPureLinkDevice

class DysonPureLink:
    #define class variables
    enabled = False
    IThinkIAmConnected = False
    #unit numbers for devices to create
    #for Pure Cool models
    fanModeUnit = 1
    nightModeUnit = 2
    fanSpeedUnit = 3
    fanOscillationUnit = 4
    standbyMonitoringUnit = 5
    filterLifeUnit = 6
    qualityTargetUnit = 7
    tempHumUnit = 8
    volatileUnit = 9
    particlesUnit = 10
    sleepTimeUnit = 11
    runCounter = 0
    
    def __init__(self):
        self.client = None
        self.config = None
        self.connected = Queue()
        self.disconnected = Queue()
        self.state_data_available = Queue()
        self.sensor_data_available = Queue()
        self.sensor_data = None
        self.state_data = None
        self._is_connected = None
        self.dyson_pure_link = None

    def onStart(self):
        Domoticz.Log("onStart called")
        if Parameters['Mode4'] == 'Debug':
            Domoticz.Debugging(1)
            DumpConfigToLog()
        
        #PureLink needs polling, get from config
        self.runCounter = int(Parameters["Mode5"])
        Domoticz.Heartbeat(10)
        
        #check, per device, if it is created. If not,create it
        if self.fanModeUnit not in Devices:
            Domoticz.Device(Name='Fan mode', Unit=self.fanModeUnit, Type=244, Subtype=62, Switchtype=0).Create()
        if self.nightModeUnit not in Devices:
            Domoticz.Device(Name='Night mode', Unit=self.nightModeUnit, Type=244, Subtype=62,  Switchtype=0).Create()
            
        Options = {"LevelActions" : "|||||||||||",
                   "LevelNames" : "|Auto|L1|L2|L3|L4|L5|L6|L7|L8|L9|L10",
                   "LevelOffHidden" : "true",
                   "SelectorStyle" : "1"}
        if self.fanSpeedUnit not in Devices:
            Domoticz.Device(Name='Fan speed', Unit=self.fanSpeedUnit, TypeName="Selector Switch", Image=7, Options=Options).Create()

        if self.fanOscillationUnit not in Devices:
            Domoticz.Device(Name='Oscilation mode', Unit=self.fanOscillationUnit, Type=244, Subtype=62,  Switchtype=0).Create()
        if self.standbyMonitoringUnit not in Devices:
            Domoticz.Device(Name='Standby monitor', Unit=self.standbyMonitoringUnit, Type=244, Subtype=62,  Switchtype=0).Create()
        if self.filterLifeUnit not in Devices:
            Domoticz.Device(Name='Remaining filter life', Unit=self.filterLifeUnit, TypeName="Custom").Create()
        if self.qualityTargetUnit not in Devices:
            Domoticz.Device(Name='Air quality setpoint', Unit=self.qualityTargetUnit, TypeName="Custom").Create()
        if self.tempHumUnit not in Devices:
            Domoticz.Device(Name='Temperature and Humidity', Unit=self.tempHumUnit, TypeName="Temp+Hum").Create()

        #read out parameters
        self.ip_address = Parameters["Address"].replace(" ", "")
        self.port_number = int(Parameters["Port"].replace(" ", ""))
        self.serial_number = Parameters['Mode2']
        self.device_type = Parameters['Mode1']
        self.password = Parameters['Password']

        # Connect device and print result
        Domoticz.Log('Connected: ' + str(self.connect_device()))

        
    def connect_device(self):
        """
        Connects to device using provided connection arguments

        Returns: True/False depending on the result of connection
        """

        Domoticz.Debug("DysonPureLink: connect device")
        #Domoticz.Trace(True) #start trace
        self.client = mqtt.Client(clean_session=True, protocol=mqtt.MQTTv311, userdata=self)
        self.client.username_pw_set(self.serial_number, self._hashed_password())
        #connect callbacks to MQTT client iso domoticz since we don't use Domoticz connection object
        self.client.on_connect = self.onConnect
        self.client.on_disconnect = self.onDisconnect
        self.client.on_message = self.onMessage
        try:
            self.client.connect(self.ip_address, port=self.port_number)
            Domoticz.Debug("Connection made, yeaha!") 
        except ConnectionRefusedError as e:
            self.IThinkIAmConnected = False
            Domoticz.Error("Connect device: Connection Refused")
            #Domoticz.Trace(False) #stop trace
            return False
        
        #Domoticz.Trace(False) #stop trace
        self.client.loop_start()

        #self._is_connected = self.connected.get(timeout=15)

        if self._is_connected:
            self._request_state()

            self.state_data = self.state_data_available.get(timeout=5)
            self.sensor_data = self.sensor_data_available.get(timeout=5)

            # Return True in case of successful connect and data retrieval
            self.IThinkIAmConnected = True
            return True

        # If any issue occurred return False
        self.client = None
        self.IThinkIAmConnected = False
        return False

    @property
    def has_valid_data(self):
        return self.sensor_data and self.sensor_data.has_data

    @property
    def device_command(self):
        return '{0}/{1}/command'.format(self.device_type, self.serial_number)

    @property
    def device_status(self):
        return '{0}/{1}/status/current'.format(self.device_type, self.serial_number)

    def _request_state(self):
        """Publishes request for current state message"""
        if self.client:
            command = json.dumps({
                    'msg': 'REQUEST-CURRENT-STATE',
                    'time': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())})
            
            self.client.publish(self.device_command, command);

    def _change_state(self, data):
        """Publishes request for change state message"""
        if self.client:
            
            command = json.dumps({
                'msg': 'STATE-SET',
                'time': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                'mode-reason': 'LAPP',
                'data': data
            })

            self.client.publish(self.device_command, command, 1)

            self.state_data = self.state_data_available.get(timeout=5)

    def _hashed_password(self):
        """Hash password (found in manual) to a base64 encoded of its shad512 value"""
        hash = hashlib.sha512()
        hash.update(self.password.encode('utf-8'))
        return base64.b64encode(hash.digest()).decode('utf-8')


    def get_data(self):
        return (self.state_data, self.sensor_data) if self.has_valid_data else tuple()

    def request_data(self):
        """send requets for new data to device"""
        if self.IThinkIAmConnected:
            self._request_state()

            self.state_data = self.state_data_available.get(timeout=5)
            self.sensor_data = self.sensor_data_available.get(timeout=5)

            # Return data in case of successful connect and data retrieval
            return (self.state_data, self.sensor_data) if self.has_valid_data else ('noValidData','noValidData')
        else:
            return ('disconnected','disconnected')
        
    def disconnect_device(self):
        """Disconnects device and return the boolean result"""
        Domoticz.Debug("DysonPureLink: disconnect_device")
        if self.client:
            self.client.loop_stop()
            self.client.disconnect()
            self.IThinkIAmConnected = False
            
            # Wait until we get on disconnect message
            # self._is_connected = not(self.disconnected.get(timeout=5))
            # return self._is_connected
            return self.disconnected.get(timeout=5)

        
    def onStop(self):
        Domoticz.Log("DysonPureLink: onStop called")
        # if self.IThinkIAmConnected:
            # Domoticz.Debug('onStop: Try to disconnect')
            # self.disconnect_device()
        # Domoticz.Debug('Disconnected')

    @staticmethod
    def onConnect(client, userdata, flags, return_code):
        """Static callback to handle on_connect event"""
        Domoticz.Log("DysonPureLink: onConnect called")
        dummyBool=True
        Domoticz.Debug("onConnect: return_code: " + str(return_code))
        Domoticz.Debug("onConnect: flags: " + str(flags) + ", self.IThinkIAmConnected: " + str(self.IThinkIAmConnected))

        # Connection is successful with return_code: 0
        if return_code:
            userdata.connected.put_nowait(False)
            raise ConnectionError(return_code)

        # We subscribe to the status message
        client.subscribe(userdata.device_status)
        userdata.connected.put_nowait(True)
        self.IThinkIAmConnected = True

    @staticmethod
    def onMessage(client, userdata, message):
        """Static callback to handle incoming messages"""
        Domoticz.Log("DysonPureLink: onMessage called")
        payload = message.payload.decode("utf-8")
        json_message = json.loads(payload)
        Domoticz.Debug("spit the json_message:" + json_message)
        
        if StateData.is_state_data(json_message):
            userdata.state_data_available.put_nowait(StateData(json_message))

        if SensorsData.is_sensors_data(json_message):
            userdata.sensor_data_available.put_nowait(SensorsData(json_message))

    def onCommand(self, Unit, Command, Level, Hue):
        Domoticz.Log("DysonPureLink: onCommand called for Unit " + str(Unit) + ": Parameter '" + str(Command) + "', Level: " + str(Level))

    def onNotification(self, Name, Subject, Text, Status, Priority, Sound, ImageFile):
        Domoticz.Log("DysonPureLink: onNotification: " + Name + "," + Subject + "," + Text + "," + Status + "," + str(Priority) + "," + Sound + "," + ImageFile)

    @staticmethod
    def onDisconnect(client, userdata, return_code):
        """Static callback to handle on_disconnect event"""
        Domoticz.Debug("onDisconnect: return_code: " + return_code)
        Domoticz.Log("DysonPureLink: onDisconnect called")
        #self._is_connected = False
        if return_code:
            raise DisconnectionError(return_code)

        userdata.disconnected.put_nowait(True)

    def onHeartbeat(self):
        Domoticz.Log("DysonPureLink: onHeartbeat called, version: " + Parameters["Version"])
        self.runCounter = self.runCounter - 1
        if self.runCounter <= 0:
            Domoticz.Debug("Poll unit")
            self.runCounter = int(Parameters["Mode5"])
            if not self.IThinkIAmConnected:
                Domoticz.Debug("DysonPureLink: onHeartbeat, try to reconnect")
                self.connect_device()
            #return True
            # Get and print state and sensors data
            if self.IThinkIAmConnected:
                Domoticz.Debug("unit is connected, lets show some  data")
                for entry in self.request_data():
                    Domoticz.Debug(str(entry))
                #self.disconnect_device()
            else:
                Domoticz.Debug("unit not connected")

    def onDeviceRemoved(self):
        Domoticz.Log("DysonPureLink: onDeviceRemoved called")
        
global _plugin
_plugin = DysonPureLink()

def onStart():
    global _plugin
    _plugin.onStart()

def onStop():
    global _plugin
    _plugin.onStop()

def onConnect(Connection, Status, Description):
    global _plugin
    Domoticz.Debug("base plugin onConnect, skipping it")
    #_plugin.onConnect(Connection, Status, Description)

def onMessage(Connection, Data):
    global _plugin
    #_plugin.onMessage(Connection, Data)
    Domoticz.Debug("base plugin onMessage, skipping it")


def onCommand(Unit, Command, Level, Hue):
    global _plugin
    _plugin.onCommand(Unit, Command, Level, Hue)

def onNotification(Name, Subject, Text, Status, Priority, Sound, ImageFile):
    global _plugin
    _plugin.onNotification(Name, Subject, Text, Status, Priority, Sound, ImageFile)

def onDisconnect(Connection):
    global _plugin
    #_plugin.onDisconnect(Connection)
    Domoticz.Debug("base plugin onDisconnect, skipping it")

def onHeartbeat():
    global _plugin
    _plugin.onHeartbeat()

def onDeviceRemoved():
    global _plugin
    _plugin.onDeviceRemoved()

    # Generic helper functions
def DumpConfigToLog():
    for x in Parameters:
        if Parameters[x] != "":
            Domoticz.Debug( "'" + x + "':'" + str(Parameters[x]) + "'")
    Domoticz.Debug("Device count: " + str(len(Devices)))
    for x in Devices:
        Domoticz.Debug("Device:           " + str(x) + " - " + str(Devices[x]))
        Domoticz.Debug("Device ID:       '" + str(Devices[x].ID) + "'")
        Domoticz.Debug("Device Name:     '" + Devices[x].Name + "'")
        Domoticz.Debug("Device nValue:    " + str(Devices[x].nValue))
        Domoticz.Debug("Device sValue:   '" + Devices[x].sValue + "'")
        Domoticz.Debug("Device LastLevel: " + str(Devices[x].LastLevel))
    return