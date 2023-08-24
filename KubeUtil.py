import xml.etree.ElementTree as ET
import os
import sys
import time
import traceback
import errno
import json
import collections

import copy
from six.moves.urllib.parse import urlencode
from com.manageengine.monagent.logger import AgentLogger
from com.manageengine.monagent.communication import CommunicationHandler
from com.manageengine.monagent import AgentConstants
from com.manageengine.monagent.apps import persist_data as appData
from com.manageengine.monagent.scheduler import AgentScheduler
from com.manageengine.monagent.util import AgentUtil
from com.manageengine.monagent.util.AgentUtil import ZipUtil,FileUtil
from com.manageengine.monagent.kubernetes import KubeGlobal
from _ast import IsNot
from logging import root
from multiprocessing import Condition
from itertools import islice


idDict = {}

def load_xml_root_from_file(xmlFileName):
    root = None
    file_obj = None
    try:
        if os.path.isfile(xmlFileName):
            AgentLogger.debug(AgentLogger.KUBERNETES,'LoadXmlRootFromFile -> xmlFileName available')
            file_obj = open(xmlFileName,'rb')
            byte_data = file_obj.read()
            fileSysEncoding = sys.getfilesystemencoding()
            perfData = byte_data.decode(fileSysEncoding)
            root = ET.fromstring(perfData)
        else:
            AgentLogger.log(AgentLogger.KUBERNETES,'LoadXmlRootFromFile -> xmlFileName not available')
    except Exception as e:
        AgentLogger.log(AgentLogger.KUBERNETES,'LoadXmlRootFromFile -> Exception -> {0}'.format(e))
    finally:
        if file_obj:
            file_obj.close()
    return root

def curl_api_without_token(url):
    try:
        proxies = {"http": None,"https": None}        
        r = AgentConstants.REQUEST_MODULE.get(url,proxies=proxies,verify=False,timeout=KubeGlobal.urlTimeout)
        AgentLogger.log(AgentLogger.KUBERNETES,'curlapiWithoutToken -> url - ' + url)
        AgentLogger.log(AgentLogger.KUBERNETES,'curlapiWithoutToken -> statusCode - ' + str(r.status_code))
        data = r.content
        if isinstance(data, bytes):
            data = data.decode()
        return r.status_code,data
    except Exception as e:
        AgentLogger.log(AgentLogger.KUBERNETES,'curlapiWithoutToken -> Exception -> {0}'.format(e))
    return -1,{}

def curl_api_with_token(url):
        try:            
            headers = {'Authorization' : 'Bearer ' + KubeGlobal.bearerToken}
            proxies = {"http": None,"https": None}
            r = AgentConstants.REQUEST_MODULE.get(url,headers=headers,proxies=proxies,verify=False,timeout=KubeGlobal.urlTimeout)
            AgentLogger.log(AgentLogger.KUBERNETES,'curlapiWithToken -> url - ' + url)
            AgentLogger.log(AgentLogger.KUBERNETES,'curlapiWithToken -> statusCode - ' + str(r.status_code))
            data = r.content
            if isinstance(data, bytes):
                data = data.decode()
            if "/metrics/cadvisor" in url:
                return r.status_code,data
            return r.status_code,json.loads(data)
        except Exception as e:
            AgentLogger.log(AgentLogger.KUBERNETES,'curlapiWithToken -> Exception -> {0}'.format(e))
        return -1,{}

def get_cluster_DNS():
    try:
        status_code, json=curl_api_with_token(KubeGlobal.host+KubeGlobal.clusterDNPATH)
        if "items" in json.keys():
            json=json.get("items")[0]
            if "data" in json.keys():
                json=json.get("data")
                if "kubeconfig" in json.keys():
                    json=json.get("kubeconfig").split("\n")
        for i in json:
            if "server:" in i:
                dns_name=i.strip().split(': ')[1]
                dns_split=dns_name.split('.')[0].split('//')[1]
                dns_name=dns_name.replace(dns_split, dns_split.upper())
                AgentLogger.log(AgentLogger.KUBERNETES, "Cluster DNS name: "+dns_name)
                return dns_name
        AgentLogger.log(AgentLogger.KUBERNETES, 'Fargate Cluster DNS not present in the configmap')
        return None
    except Exception as e:
        AgentLogger.log(AgentLogger.KUBERNETES, 'Exception While Fetching the Fargate Cluster DNS Name -> {0}'.format(e))
        return None
    
def clear_and_init_dict(thisDic):
        try:
            if thisDic:
                thisDic.clear()
            thisDic = {}
        except Exception as e:
            AgentLogger.log(AgentLogger.KUBERNETES,'clearAndInitDict -> Exception -> {0}'.format(e))
            
def get_dict_node(dictNode,path):
    try:
        toReturnNode = dictNode
        pathExists = False
        #AgentLogger.log(AgentLogger.KUBERNETES,str(path))
        if path is not "":
            for patharg in path.split('/'):
                if patharg=="#":
                    patharg="/"
                
                if patharg in toReturnNode:
                    tempNode = toReturnNode[patharg]
                    toReturnNode = tempNode
                    pathExists = True
                else:
                    AgentLogger.debug(AgentLogger.KUBERNETES,'path - ' + str(patharg) + 'does not exist')
                    pathExists = False
                    break
                
        if pathExists:
            return toReturnNode
    except Exception as e:
        AgentLogger.log(AgentLogger.KUBERNETES,'GetItemsGroupNode -> Exception -> {0}'.format(e))
    return None

#This function is not used currently... for future needs it can be called
def load_from_id_file(idFile):
    AgentLogger.log(AgentLogger.KUBERNETES,'LoadFromIDFile')
    file_obj = None
    try:
        if os.path.isfile(idFile):                
            file_obj = open(idFile,'rb')
            byte_data = file_obj.read()
            fileSysEncoding = sys.getfilesystemencoding()
            idData = byte_data.decode(fileSysEncoding)
            AgentLogger.debug(AgentLogger.KUBERNETES,idData)
            idDict = json.loads(idData)
        else :
            AgentLogger.log(AgentLogger.KUBERNETES,'LoadFromIDFile -> file not found ')
    except Exception as e:
        AgentLogger.log(AgentLogger.KUBERNETES,'LoadFromIDFile -> Exception -> {0}'.format(e))
    finally:
        if file_obj:
            file_obj.close()
            
def get_kube_ids():
    try:
        if AgentConstants.APPS_CONFIG_DATA:
            if "KUBERNETES" in AgentConstants.APPS_CONFIG_DATA:
                KubeGlobal.kubeIds = AgentConstants.APPS_CONFIG_DATA["KUBERNETES"]
            else:
                AgentLogger.log(AgentLogger.KUBERNETES,'get_kube_ids -> "apps" not found ')
        else:
            AgentLogger.log(AgentLogger.KUBERNETES,'get_kube_ids -> "AgentConstants.APPS_CONFIG_DATA" is empty')
    except Exception as e:
        AgentLogger.log(AgentLogger.KUBERNETES,'get_id_group_dict -> Exception -> {0}'.format(e))

def get_id_group_dict(group):
    try:
        if KubeGlobal.kubeIds:
            if group in KubeGlobal.kubeIds:
                return KubeGlobal.kubeIds[group]
            else:
                AgentLogger.debug(AgentLogger.KUBERNETES,'group not found in id dict - {0}'.format(group))
        else:
            AgentLogger.log(AgentLogger.KUBERNETES,'kubeGlobal.kubeIds is empty')
    except Exception as e:
        AgentLogger.log(AgentLogger.KUBERNETES,'get_id_group_dict -> Exception -> {0}'.format(e))
    return None

def get_id(groupIdDict,itemName):
    id = ""
    try:
        if groupIdDict:
            if itemName in groupIdDict:
                itemHash = groupIdDict[itemName]
                id = itemHash["id"]
            else:
                AgentLogger.debug(AgentLogger.KUBERNETES,'id for itemName not found - {0}'.format(itemName))
        else:
            AgentLogger.debug(AgentLogger.KUBERNETES,'group ID dict is empty')            
    except Exception as e:
        AgentLogger.log(AgentLogger.KUBERNETES,'get_id -> Exception -> {0}'.format(e))
    return id

def get_resource_id(resource_name,resource_group):
    id = None
    try:
        resource_config = get_id_group_dict(resource_group)
        if resource_config:
            if resource_name in resource_config:
                id = resource_config[resource_name]['id']
        AgentLogger.log(AgentLogger.KUBERNETES,'id :: {} - name :: {}'.format(id,resource_name))
    except Exception as e:
        traceback.print_exc()
    return id
    
def map_container_ids(dataDic):
    try:
        AgentLogger.log(AgentLogger.KUBERNETES,'mapping container ids')
        if dataDic and "Pods" in dataDic:
            #get podsData
            podsData = dataDic["Pods"]
            AgentLogger.log(AgentLogger.KUBERNETES,'map_container_ids -> Pods present in dataDic')
            #get podsId
            podsId = get_id_group_dict("Pods")
            if podsId:
                AgentLogger.log(AgentLogger.KUBERNETES,'map_container_ids -> Pods present in idDic')
                
                for pod,podData in podsData.items():
                    if pod in podsId:
                        podId = podsId[pod]
                        #get cont data
                        if "Cont" in podData:
                            contData = podData["Cont"]                            
                            #get corresponding cont id
                            if "Cont" in podId:
                                contIDs = podId["Cont"]
                                #map cont IDs
                                for cont in contData:
                                    id = get_id(contIDs,cont)
                                    AgentLogger.debug(AgentLogger.KUBERNETES,'map_container_ids -> id found - {0}'.format(cont))
                                    contData[cont]["id"] = id
                    else:
                        AgentLogger.log(AgentLogger.KUBERNETES,'map_container_ids -> Pod not present in podsId - {0}'.format(pod))
                    AgentLogger.debug(AgentLogger.KUBERNETES,'pod - {0}'.format(pod))
                    AgentLogger.debug(AgentLogger.KUBERNETES,json.dumps(podData))    
    except Exception as e:
        AgentLogger.log(AgentLogger.KUBERNETES,'map_conatiner_ids -> Exception -> {0}'.format(e))
    return dataDic

def saveKubeData(kube_data):
    status = True
    file_name = None
    try:
        kube_data['MSPCUSTOMERID'] = AgentConstants.CUSTOMER_ID
        kube_data['AGENTKEY'] = AgentUtil.AGENT_CONFIG.get('AGENT_INFO', 'agent_key')
        file_name = FileUtil.getUniqueFileName(AgentUtil.AGENT_CONFIG.get('AGENT_INFO', 'agent_key'))
        if file_name:
            file_path = os.path.join(AgentConstants.AGENT_UPLOAD_PROPERTIES_MAPPER['003']['data_path'], file_name)
            file_obj = AgentUtil.FileObject()
            file_obj.set_fileName(file_name)
            file_obj.set_filePath(file_path)
            file_obj.set_data(kube_data)
            file_obj.set_dataType('json' if type(kube_data) is dict else "xml")
            file_obj.set_mode('wb')
            file_obj.set_dataEncoding('UTF-16LE')
            file_obj.set_loggerName(AgentLogger.KUBERNETES)
            status, file_path = FileUtil.saveData(file_obj)
    except Exception as e:
        AgentLogger.log([AgentLogger.KUBERNETES,AgentLogger.STDERR], '*************************** Exception while saving kubernetes collected data in data directory : '+'*************************** '+ repr(e) + '\n')
        traceback.print_exc()
        status = False
    return status, file_name

def write_data_to_file(data_files_list):
    file_name = None
    status = False
    data_files_path_list = []
    try:
        if data_files_list:
            for each_data in data_files_list:
                #add mid
                if not ('mid' in each_data):
                    each_data["mid"] = KubeGlobal.mid
                #add child count
                each_data["spc"] = KubeGlobal.childWriteCount
                #write to file
                status,file_name = saveKubeData(each_data)
                data_files_path_list.append(file_name)
                AgentLogger.log(AgentLogger.KUBERNETES,'Kube filename - {0}'.format(file_name))
            #zip file
            if data_files_path_list:
                if AgentConstants.AGENT_UPLOAD_PROPERTIES_MAPPER['003']['instant_zip']:
                    ZipUtil.zipFilesAtInstance([data_files_path_list],AgentConstants.AGENT_UPLOAD_PROPERTIES_MAPPER['003'])
        else:
            AgentLogger.log(AgentLogger.KUBERNETES,'WriteDataToFile :: data is empty')
    except Exception as e:
        AgentLogger.log(AgentLogger.KUBERNETES,'WriteDataToFile -> Exception -> {0}'.format(e))

def split_data(data):
    try:
        splited_data_files_list = []
        if data:
            ct = int(AgentUtil.getTimeInMillis())
            prevChildDict = {}
            finalFileDict = {}
            chunkSize = int(KubeGlobal.childWriteCount)
            currPrevCount = 0
            splitNumber = 0
            pushFlag = False

            perf = "false"
            if "perf" in data:
                perf = data["perf"]
                del data["perf"]
            allPerf = None
            if "allperf" in data:
                allPerf = data["allPerf"]
                del data["allPerf"]

            for k, v in data.items():
                if k == "k8s_events": continue

                if k == "kubernetes":
                    finalFileDict = {}
                    finalFileDict[k] = v
                    finalFileDict["ct"] = ct
                    finalFileDict["perf"] = perf
                    splited_data_files_list.append(copy.deepcopy(finalFileDict))

                if type(v) is dict:
                    for v1 in AgentUtil.dict_chunks(v, chunkSize):
                        splitNumber += 1
                        finalFileDict = {}
                        finalFileDict[k] = v1
                        finalFileDict["ct"] = ct
                        finalFileDict["perf"] = perf
                        splited_data_files_list.append(copy.deepcopy(finalFileDict))

                        clear_and_init_dict(finalFileDict)
            write_data_to_file(splited_data_files_list)
    except Exception as e:
        traceback.print_exc()


# def split_data1(data):
#     try:
#         splited_data_files_list = []
#         if data:
#             ct = int(AgentUtil.getTimeInMillis())
#             prevChildDict = {}
#             finalFileDict = {}
#             chunkSize = int(KubeGlobal.childWriteCount)
#             currPrevCount = 0
#             splitNumber = 0
#             pushFlag = False
# 
#             perf = "false"
#             if "perf" in data:
#                 perf = data["perf"]
#                 del data["perf"]
#             allPerf = None
#             if "allperf" in data:
#                 allPerf = data["allPerf"]
#                 del data["allPerf"]
# 
#             for k, v in data.items():
#                 if k == "k8s_events": continue
# 
#                 if type(v) is dict:
#                     for v1 in AgentUtil.dict_chunks(v, chunkSize):
#                         vLen = len(v1)
#                         if currPrevCount + vLen == chunkSize:
#                             finalFileDict = prevChildDict.copy()
#                             finalFileDict[k] = v1
#                             clear_and_init_dict(prevChildDict)
#                             currPrevCount = 0
#                             splitNumber += 1
#                             pushFlag = True
#                         elif currPrevCount + vLen < chunkSize:
#                             prevChildDict[k] = v1
#                             currPrevCount += vLen
#                         else:
#                             prevChildDict[k] = dict(islice(v1.items(), chunkSize-currPrevCount))
#                             finalFileDict = prevChildDict.copy()
#                             clear_and_init_dict(prevChildDict)
#                             prevChildDict[k] = dict(islice(v1.items(), chunkSize-currPrevCount, vLen))
#                             currPrevCount = currPrevCount + vLen - chunkSize
#                             splitNumber += 1
#                             pushFlag = True
# 
#                         if pushFlag:
#                             pushFlag = False
#                             if currPrevCount == 0 and k == list(data.keys())[-1]:
#                                 AgentLogger.log(AgentLogger.KUBERNETES,'last zip count - {0}'.format(splitNumber))
#                                 finalFileDict["lastzip"] = "true"
# 
#                             if allPerf:
#                                 finalFileDict["allPerf"] = allPerf
#                             finalFileDict["ct"] = ct
#                             finalFileDict["perf"] = perf
#                             splited_data_files_list.append(copy.deepcopy(finalFileDict))
#                             clear_and_init_dict(finalFileDict)
#                 else:
#                     prevChildDict[k]=v
# 
#             if currPrevCount > 0:
#                 splitNumber += 1
#                 AgentLogger.log(AgentLogger.KUBERNETES,'last zip count - {0}'.format(splitNumber))
#                 finalFileDict = prevChildDict
#                 finalFileDict["lastzip"] = "true"
#                 if allPerf:
#                     finalFileDict["allPerf"] = allPerf
#                 finalFileDict["ct"] = ct
#                 finalFileDict["perf"] = perf
#                 splited_data_files_list.append(copy.deepcopy(finalFileDict))
#                 clear_and_init_dict(finalFileDict)
# 
#             if "k8s_events" in data and len(data["k8s_events"]) > 0:
#                 for eventsChunks in AgentUtil.list_chunks(data["k8s_events"], int(KubeGlobal.eventsWriteCount)):
#                     splited_data_files_list.append(eventsChunks)
# 
#             write_data_to_file(splited_data_files_list)
#     except Exception as e:
#         traceback.print_exc()
#         AgentLogger.log(AgentLogger.KUBERNETES, "Exception while Splitting {}".format(e))
        
def mark_deleted_data(dataDic):
    try:
        AgentLogger.log(AgentLogger.KUBERNETES,'inside mark_deleted_data....')
        if KubeGlobal.kubeIds:
            if dataDic:
                for k,v in KubeGlobal.kubeIds.items():
                    AgentLogger.log(AgentLogger.KUBERNETES,'mark_deleted_data -> processing - {0}'.format(k))
                    if isinstance(v,dict) and k in dataDic:
                        itemDataDic = dataDic[k]
                        for k1,v1 in v.items():
                            if k in KubeGlobal.NO_NS_UNIQUNESS_TYPES:
                                search_key = k1
                            else:
                                search_key = k1+"_"
                            res = dict(filter(lambda item: search_key in item[0], itemDataDic.items()))
                            if not res and isinstance(v1,dict):
                                AgentLogger.log(AgentLogger.KUBERNETES,'mark_deleted_data -> marking deleted - {0}'.format(k1))
                                temp = v1
                                temp["deleted"] = "true"
                                itemDataDic[k1] = temp
                    else:
                        AgentLogger.log(AgentLogger.KUBERNETES,'mark_deleted_data -> no data for - {0}'.format(k))
        else:
            AgentLogger.log(AgentLogger.KUBERNETES,'mark_deleted_data -> kubeIds is empty')
    except Exception as e:
        AgentLogger.log(AgentLogger.KUBERNETES,'mark_deleted_data -> Exception -> {0}'.format(e))
        
def setKubeStateMetricsUrl():
    AgentLogger.log(AgentLogger.KUBERNETES,'getKubeStateMetricsUrl')
    try:
        if not KubeGlobal.kubeStateMetricsUrl:
            if KubeGlobal.kubeStatePodIP:
                KubeGlobal.kubeStateMetricsUrl = "http://" + KubeGlobal.kubeStatePodIP + ":" + KubeGlobal.kubeStatePort
                AgentLogger.log(AgentLogger.KUBERNETES,'kubeStateMetricsUrl - {0}'.format(KubeGlobal.kubeStateMetricsUrl))
            else:
                AgentLogger.log(AgentLogger.KUBERNETES,'kubeStateMetricsUrl - kubeStatePodIP is null')
        else:
            AgentLogger.log(AgentLogger.KUBERNETES,'kubeStateMetricsUrl already set....')
    except Exception as e:
        AgentLogger.log(AgentLogger.KUBERNETES,'getKubeStateMetricsUrl -> Exception -> {0}'.format(e))   

def replace_tokens(data):
    try:
        if data:
            if KubeGlobal.fargate=='1' and KubeGlobal.nodeName and "$NODE_IP$" in data:
                path=data.split("$/")[1]
                path_temp=path.split("/")
                if "stats" in path_temp or "metrics" in path_temp:
                    data=KubeGlobal.apiEndpoint+KubeGlobal.kubeletPath+KubeGlobal.nodeName+'/proxy/'+path
                    AgentLogger.log(AgentLogger.KUBERNETES, 'Replacing Token for Fargate Proxy Kubelet Api')

            if "$NODE_IP$" in data and AgentConstants.IP_ADDRESS:
                AgentLogger.log(AgentLogger.KUBERNETES,'ReplaceTokens :: Replacing $NODE_IP$')
                data = data.replace("$NODE_IP$",AgentConstants.IP_ADDRESS)
            if "$KUBELET_STATS_PORT$" in data and KubeGlobal.kubeletStatsPort:
                AgentLogger.log(AgentLogger.KUBERNETES,'ReplaceTokens :: Replacing $KUBELET_STATS_PORT$ - {0}'.format(KubeGlobal.kubeletStatsPort))
                data = data.replace("$KUBELET_STATS_PORT$",KubeGlobal.kubeletStatsPort)
            if "$KUBE_STATE_METRICS_URL$" in data and KubeGlobal.kubeStateMetricsUrl:
                AgentLogger.log(AgentLogger.KUBERNETES,'ReplaceTokens :: Replacing $KUBE_STATE_METRICS_URL$ - {0}'.format(KubeGlobal.kubeStateMetricsUrl))
                data = data.replace("$KUBE_STATE_METRICS_URL$",KubeGlobal.kubeStateMetricsUrl)
            if "$KUBE_STATE_IP$" in data and KubeGlobal.kubeStatePodIP:
                AgentLogger.log(AgentLogger.KUBERNETES,'ReplaceTokens :: Replacing $KUBE_STATE_IP$ - {0}'.format(KubeGlobal.kubeStatePodIP))
                data = data.replace("$KUBE_STATE_IP$",KubeGlobal.kubeStatePodIP)
            if "$KUBE_STATE_PORT$" in data and KubeGlobal.kubeStatePort:
                AgentLogger.log(AgentLogger.KUBERNETES,'ReplaceTokens :: Replacing $KUBE_STATE_PORT$ - {0}'.format(KubeGlobal.kubeStatePort))
                data = data.replace("$KUBE_STATE_PORT$",KubeGlobal.kubeStatePort)
    except Exception as e:
        AgentLogger.log(AgentLogger.KUBERNETES,'ReplaceTokens -> Exception -> {0}'.format(e))
    return data
        
