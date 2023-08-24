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
from com.manageengine.monagent.util import AgentUtil
from com.manageengine.monagent.util.AgentUtil import FileUtil, ZipUtil
from com.manageengine.monagent.scheduler import AgentScheduler
from com.manageengine.monagent.kubernetes import KubeGlobal
from _ast import IsNot
from logging import root
from multiprocessing import Condition
from itertools import islice

if 'com.manageengine.monagent.kubernetes.EventCollector' in sys.modules:
    EventCollector = sys.modules['com.manageengine.monagent.kubernetes.EventCollector']
else:
    from com.manageengine.monagent.kubernetes import EventCollector

idDict = {}


def load_xml_root_from_file(xmlFileName):
    root = None
    file_obj = None
    try:
        if os.path.isfile(xmlFileName):
            AgentLogger.debug(AgentLogger.KUBERNETES, 'LoadXmlRootFromFile -> xmlFileName available')
            file_obj = open(xmlFileName, 'rb')
            byte_data = file_obj.read()
            fileSysEncoding = sys.getfilesystemencoding()
            perfData = byte_data.decode(fileSysEncoding)
            root = ET.fromstring(perfData)
        else:
            AgentLogger.log(AgentLogger.KUBERNETES, 'LoadXmlRootFromFile -> xmlFileName not available')
    except Exception as e:
        AgentLogger.log(AgentLogger.KUBERNETES, 'LoadXmlRootFromFile -> Exception -> {0}'.format(e))
    finally:
        if file_obj:
            file_obj.close()
    return root


def curl_api_without_token(url):
    try:
        proxies = {"http": None, "https": None}
        r = AgentConstants.REQUEST_MODULE.get(url, proxies=proxies, verify=False, timeout=KubeGlobal.urlTimeout)
        AgentLogger.log(AgentLogger.KUBERNETES, 'curlapiWithoutToken -> url - ' + url)
        AgentLogger.log(AgentLogger.KUBERNETES, 'curlapiWithoutToken -> statusCode - ' + str(r.status_code))
        data = r.content
        if isinstance(data, bytes):
            data = data.decode()
        return r.status_code, data
    except Exception as e:
        AgentLogger.log(AgentLogger.KUBERNETES, 'curlapiWithoutToken -> Exception -> {0}'.format(e))
    return -1, {}


def curl_api_with_token(url):
    try:
        headers = {'Authorization': 'Bearer ' + KubeGlobal.bearerToken}
        proxies = {"http": None, "https": None}
        r = AgentConstants.REQUEST_MODULE.get(url, headers=headers, proxies=proxies, verify=False,
                                              timeout=KubeGlobal.urlTimeout)
        AgentLogger.log(AgentLogger.KUBERNETES, 'curlapiWithToken -> url - ' + url)
        AgentLogger.log(AgentLogger.KUBERNETES, 'curlapiWithToken -> statusCode - ' + str(r.status_code))
        data = r.content
        if isinstance(data, bytes):
            data = data.decode()
        if "/metrics/cadvisor" in url or '/healthz' in url or '/livez' in url:
            return r.status_code, data
        return r.status_code, json.loads(data)
    except Exception as e:
        AgentLogger.log(AgentLogger.KUBERNETES, 'curlapiWithToken -> Exception -> {0}'.format(e))
    return -1, {}


def get_cluster_DNS():
    try:
        dns_name = None
        if KubeGlobal.fargate == '1':
            dns_name = get_fargate_clustername()
            if dns_name:
                return dns_name

        status_code, json = curl_api_with_token(
            KubeGlobal.host + '/api/v1/endpoints?fieldSelector=metadata.name=kubernetes')
        port = "443"
        kube_endpoint = json["items"][0]
        if "subsets" in kube_endpoint and "addresses" in kube_endpoint["subsets"][0]:
            controlplane_ip = kube_endpoint["subsets"][0]["addresses"][0]["ip"]

            if "ports" in kube_endpoint["subsets"][0] and "port" in kube_endpoint["subsets"][0]["ports"][0]:
                port = kube_endpoint["subsets"][0]["ports"][0]["port"]

            AgentLogger.log(AgentLogger.KUBERNETES, "Cluster DNS name: " + controlplane_ip)
            return "https://" + controlplane_ip + ":" + str(port)
        AgentLogger.log(AgentLogger.KUBERNETES, 'controlplane_ip not present in the endpoints')
    except Exception as e:
        AgentLogger.log(AgentLogger.KUBERNETES,
                        'Exception While Fetching the Fargate Cluster DNS Name -> {0}'.format(e))
    return None


def get_fargate_clustername():
    try:
        status_code, json = curl_api_with_token(KubeGlobal.host + KubeGlobal.clusterDNPATH)
        if "items" in json.keys():
            json = json.get("items")[0]
            if "data" in json.keys():
                json = json.get("data")
                if "kubeconfig" in json.keys():
                    json = json.get("kubeconfig").split("\n")
        for i in json:
            if "server:" in i:
                dns_name = i.strip().split(': ')[1]
                dns_split = dns_name.split('.')[0].split('//')[1]
                dns_name = dns_name.replace(dns_split, dns_split.upper())
                AgentLogger.log(AgentLogger.KUBERNETES, "Cluster DNS name: " + dns_name)
                return dns_name
        AgentLogger.log(AgentLogger.KUBERNETES, 'Fargate Cluster DNS not present in the configmap')
    except Exception as e:
        AgentLogger.log(AgentLogger.KUBERNETES, "")
    return None


def clear_and_init_dict(thisDic):
    try:
        if thisDic:
            thisDic.clear()
        thisDic = {}
    except Exception as e:
        AgentLogger.log(AgentLogger.KUBERNETES, 'clearAndInitDict -> Exception -> {0}'.format(e))


def clear_and_init_list(thisList):
    try:
        if thisList:
            thisList.clear()
        thisList = []
    except Exception as e:
        AgentLogger.log(AgentLogger.KUBERNETES, 'clearAndInitList -> Exception -> {0}'.format(e))


def get_dict_node(dictNode, path):
    try:
        toReturnNode = dictNode
        pathExists = False
        # AgentLogger.log(AgentLogger.KUBERNETES,str(path))
        if path != "":
            for patharg in path.split('/'):
                if patharg == "#":
                    patharg = "/"

                if patharg in toReturnNode:
                    tempNode = toReturnNode[patharg]
                    toReturnNode = tempNode
                    pathExists = True
                else:
                    AgentLogger.debug(AgentLogger.KUBERNETES, 'path - ' + str(patharg) + 'does not exist')
                    pathExists = False
                    break

        if pathExists:
            return toReturnNode
    except Exception as e:
        AgentLogger.log(AgentLogger.KUBERNETES, 'GetItemsGroupNode -> Exception -> {0}'.format(e))
    return None


# This function is not used currently... for future needs it can be called
def load_from_id_file(idFile):
    AgentLogger.log(AgentLogger.KUBERNETES, 'LoadFromIDFile')
    file_obj = None
    try:
        if os.path.isfile(idFile):
            file_obj = open(idFile, 'rb')
            byte_data = file_obj.read()
            fileSysEncoding = sys.getfilesystemencoding()
            idData = byte_data.decode(fileSysEncoding)
            AgentLogger.debug(AgentLogger.KUBERNETES, idData)
            idDict = json.loads(idData)
        else:
            AgentLogger.log(AgentLogger.KUBERNETES, 'LoadFromIDFile -> file not found ')
    except Exception as e:
        AgentLogger.log(AgentLogger.KUBERNETES, 'LoadFromIDFile -> Exception -> {0}'.format(e))
    finally:
        if file_obj:
            file_obj.close()


def get_kube_ids():
    try:
        if AgentConstants.APPS_CONFIG_DATA:
            if "KUBERNETES" in AgentConstants.APPS_CONFIG_DATA:
                KubeGlobal.kubeIds = AgentConstants.APPS_CONFIG_DATA["KUBERNETES"]
            else:
                AgentLogger.log(AgentLogger.KUBERNETES, 'get_kube_ids -> "apps" not found ')
        else:
            AgentLogger.log(AgentLogger.KUBERNETES, 'get_kube_ids -> "AgentConstants.APPS_CONFIG_DATA" is empty')
    except Exception as e:
        AgentLogger.log(AgentLogger.KUBERNETES, 'get_id_group_dict -> Exception -> {0}'.format(e))


def get_id_group_dict(group):
    try:
        if KubeGlobal.kubeIds:
            if group in KubeGlobal.kubeIds:
                return KubeGlobal.kubeIds[group]
            else:
                AgentLogger.debug(AgentLogger.KUBERNETES, 'group not found in id dict - {0}'.format(group))
        else:
            AgentLogger.log(AgentLogger.KUBERNETES, 'kubeGlobal.kubeIds is empty')
    except Exception as e:
        AgentLogger.log(AgentLogger.KUBERNETES, 'get_id_group_dict -> Exception -> {0}'.format(e))
    return None


def get_id(groupIdDict, itemName):
    id = ""
    try:
        if groupIdDict:
            if itemName in groupIdDict:
                itemHash = groupIdDict[itemName]
                id = itemHash["id"]
            else:
                AgentLogger.debug(AgentLogger.KUBERNETES, 'id for itemName not found - {0}'.format(itemName))
        else:
            AgentLogger.debug(AgentLogger.KUBERNETES, 'group ID dict is empty')
    except Exception as e:
        AgentLogger.log(AgentLogger.KUBERNETES, 'get_id -> Exception -> {0}'.format(e))
    return id


def get_resource_id(resource_name, resource_group):
    id = None
    try:
        resource_config = get_id_group_dict(resource_group)
        if resource_config:
            if resource_name in resource_config:
                id = resource_config[resource_name]['id']
        AgentLogger.log(AgentLogger.KUBERNETES, 'id :: {} - name :: {}'.format(id, resource_name))
    except Exception as e:
        traceback.print_exc()
    return id


def map_container_ids(dataDic):
    try:
        AgentLogger.log(AgentLogger.KUBERNETES, 'mapping container ids')
        if dataDic and "Pods" in dataDic:
            # get podsData
            podsData = dataDic["Pods"]
            AgentLogger.log(AgentLogger.KUBERNETES, 'map_container_ids -> Pods present in dataDic')
            # get podsId
            podsId = get_id_group_dict("Pods")
            if podsId:
                AgentLogger.log(AgentLogger.KUBERNETES, 'map_container_ids -> Pods present in idDic')

                for pod, podData in podsData.items():
                    if pod in podsId:
                        podId = podsId[pod]
                        # get cont data
                        if "Cont" in podData:
                            contData = podData["Cont"]
                            # get corresponding cont id
                            if "Cont" in podId:
                                contIDs = podId["Cont"]
                                # map cont IDs
                                for cont in contData:
                                    id = get_id(contIDs, cont)
                                    AgentLogger.debug(AgentLogger.KUBERNETES,
                                                      'map_container_ids -> id found - {0}'.format(cont))
                                    contData[cont]["id"] = id
                    else:
                        AgentLogger.log(AgentLogger.KUBERNETES,
                                        'map_container_ids -> Pod not present in podsId - {0}'.format(pod))
                    AgentLogger.debug(AgentLogger.KUBERNETES, 'pod - {0}'.format(pod))
                    AgentLogger.debug(AgentLogger.KUBERNETES, json.dumps(podData))
    except Exception as e:
        AgentLogger.log(AgentLogger.KUBERNETES, 'map_conatiner_ids -> Exception -> {0}'.format(e))
    return dataDic


def saveKubeData(kube_data, dc_type):
    status = True
    file_name = None
    try:
        kube_data['MSPCUSTOMERID'] = AgentConstants.CUSTOMER_ID
        kube_data['AGENTKEY'] = AgentUtil.AGENT_CONFIG.get('AGENT_INFO', 'agent_key')
        file_name = FileUtil.getUniqueFileName(AgentUtil.AGENT_CONFIG.get('AGENT_INFO', 'agent_key'), dc_type)
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
        AgentLogger.log([AgentLogger.KUBERNETES, AgentLogger.STDERR],
                        '*************************** Exception while saving kubernetes collected data in data directory : ' + '*************************** ' + repr(
                            e) + '\n')
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
                dc_type = "perf"
                # add mid
                if not ('mid' in each_data):
                    each_data["mid"] = KubeGlobal.mid
                # determining dc type
                if "kubelet" in each_data:
                    dc_type = "kubelet"
                elif "perf" in each_data:
                    dc_type = "conf" if each_data["perf"] == "false" else "perf"
                # add child count
                each_data["spc"] = KubeGlobal.childWriteCount
                # write to file
                status, file_name = saveKubeData(each_data, dc_type)
                data_files_path_list.append(file_name)
                AgentLogger.log(AgentLogger.KUBERNETES, 'Kube filename - {0}'.format(file_name))
            # zip file
            if data_files_path_list:
                if AgentConstants.AGENT_UPLOAD_PROPERTIES_MAPPER['003']['instant_zip']:
                    ZipUtil.zipFilesAtInstance([data_files_path_list],
                                               AgentConstants.AGENT_UPLOAD_PROPERTIES_MAPPER['003'])
        else:
            AgentLogger.log(AgentLogger.KUBERNETES, 'WriteDataToFile :: data is empty')
    except Exception as e:
        AgentLogger.log(AgentLogger.KUBERNETES, 'WriteDataToFile -> Exception -> {0}'.format(e))

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

def mark_deleted_data(dataDic):
    try:
        AgentLogger.log(AgentLogger.KUBERNETES, 'inside mark_deleted_data....')
        if KubeGlobal.kubeIds:
            if dataDic:
                for k, v in KubeGlobal.kubeIds.items():
                    AgentLogger.log(AgentLogger.KUBERNETES, 'mark_deleted_data -> processing - {0}'.format(k))
                    if isinstance(v, dict) and k in dataDic:
                        itemDataDic = dataDic[k]
                        for k1, v1 in v.items():
                            res = dict(filter(lambda item: k1 in item[0], itemDataDic.items()))
                            if not res and isinstance(v1, dict):
                                AgentLogger.log(AgentLogger.KUBERNETES,
                                                'mark_deleted_data -> marking deleted - {0}'.format(k1))
                                temp = v1
                                temp["deleted"] = "true"
                                itemDataDic[k1] = temp
                    else:
                        AgentLogger.log(AgentLogger.KUBERNETES, 'mark_deleted_data -> no data for - {0}'.format(k))
        else:
            AgentLogger.log(AgentLogger.KUBERNETES, 'mark_deleted_data -> kubeIds is empty')
    except Exception as e:
        AgentLogger.log(AgentLogger.KUBERNETES, 'mark_deleted_data -> Exception -> {0}'.format(e))


def setKubeStateMetricsUrl():
    AgentLogger.log(AgentLogger.KUBERNETES, 'getKubeStateMetricsUrl')
    try:
        if not KubeGlobal.kubeStateMetricsUrl:
            if KubeGlobal.kubeStatePodIP:
                KubeGlobal.kubeStateMetricsUrl = "http://" + KubeGlobal.kubeStatePodIP + ":" + KubeGlobal.kubeStatePort
                AgentLogger.log(AgentLogger.KUBERNETES,
                                'kubeStateMetricsUrl - {0}'.format(KubeGlobal.kubeStateMetricsUrl))
            else:
                AgentLogger.log(AgentLogger.KUBERNETES, 'kubeStateMetricsUrl - kubeStatePodIP is null')
        else:
            AgentLogger.log(AgentLogger.KUBERNETES, 'kubeStateMetricsUrl already set....')
    except Exception as e:
        AgentLogger.log(AgentLogger.KUBERNETES, 'getKubeStateMetricsUrl -> Exception -> {0}'.format(e))


def getResourceFromLabelMap(label, type):
    resource_name = None
    lookup_dict = None
    try:
        if type == "Services":
            lookup_dict = KubeGlobal.SERVICES_LABELS_LOOKUP
        if label:
            for lk, lv in label.items():
                lookup_key = lk + "@" + lv
                if lookup_key in lookup_dict:
                    resource_name = lookup_dict[lookup_key]
                    break
    except Exception as e:
        traceback.print_exc()
    return resource_name


def createWorkloadsParentChildDict(finalDict):
    workloadsToItr = ["Pod_DependentRes", "ReplicaSet_DependentRes"]
    dependent_dict = KubeGlobal.WORKLOADS_PARENT_CHILD_DICT
    try:
        for itr in workloadsToItr:
            try:
                resType = itr.split("_")[0]
                for key, owner_info in finalDict[itr].items():
                    split_list = key.split("_")
                    child_name = split_list[0] + "_" + split_list[1]
                    workload_name = owner_info["RN"] + "_" + owner_info["NS"]

                    if owner_info["RT"] not in dependent_dict:
                        dependent_dict[owner_info["RT"]] = {}

                    if workload_name not in dependent_dict[owner_info["RT"]]:
                        dependent_dict[owner_info["RT"]][workload_name] = []

                    if resType not in dependent_dict:
                        dependent_dict[resType] = {}

                    if child_name not in dependent_dict[resType]:
                        dependent_dict[resType][child_name] = []

                    dependent_dict[owner_info["RT"]][workload_name].append(resType + "_" + child_name)
                    dependent_dict[resType][child_name].append(owner_info["RT"] + "_" + workload_name)
            except Exception as e:
                traceback.print_exc()
            finalDict.pop(itr)
    except Exception as e:
        AgentLogger.log(AgentLogger.KUBERNETES, "Exception while creating Workloads Child Dict")
    AgentLogger.debug(AgentLogger.KUBERNETES, "parentChild res {}".format(KubeGlobal.WORKLOADS_PARENT_CHILD_DICT))


def getWorkloadsDepenRes(resType, resName, withId=True):
    dependent_dict = KubeGlobal.WORKLOADS_PARENT_CHILD_DICT
    workloads_dict = {}
    try:
        if resType in dependent_dict and resName in dependent_dict[resType]:
            pod_id_dict = get_id_group_dict("Pods")
            workloads_dict["cont"] = []
            for res in dependent_dict[resType][resName]:
                res_split = res.split("_")
                resKey = res_split[1] + "_" + res_split[2]
                res_type = res_split[0]

                if res_type not in workloads_dict:
                    workloads_dict[res_type] = []

                if res_type == "Pod" and pod_id_dict:
                    if pod_id_dict and resKey in pod_id_dict and "Cont" in pod_id_dict[resKey]:
                        for ck, cv in pod_id_dict[resKey]["Cont"].items():
                            workloads_dict["cont"].append(cv["id"])

                if not withId and res_type == "ReplicaSet":
                    workloads_dict[res_type].append(resKey)
                    continue

                id = get_resource_id(resKey, res_type + "s")
                if id: workloads_dict[res_type].append(id)
    except Exception as e:
        traceback.print_exc()
    return workloads_dict


def replace_tokens(data, node="", throughProxy=False):
    try:
        if data:
            if (throughProxy or KubeGlobal.fargate == '1') and KubeGlobal.nodeName and "$NODE_IP$" in data:
                if KubeGlobal.gkeAutoPilot == '1':
                    return "https://" + node + "/stats/summary"

                if not throughProxy: node = KubeGlobal.nodeName

                path = data.split("$/")[1]
                path_temp = path.split("/")

                apiHost = getKubeAPIServerEndPoint()
                if "stats" in path_temp or "metrics" in path_temp:
                    data = apiHost + KubeGlobal.kubeletPath + node + '/proxy/' + path
                    AgentLogger.log(AgentLogger.KUBERNETES, 'Replacing Token for Fargate Proxy Kubelet Api')

            if "$NODE_IP$" in data and AgentConstants.IP_ADDRESS:
                AgentLogger.log(AgentLogger.KUBERNETES, 'ReplaceTokens :: Replacing $NODE_IP$')
                data = data.replace("$NODE_IP$", AgentConstants.IP_ADDRESS)
            if "$KUBELET_STATS_PORT$" in data and KubeGlobal.kubeletStatsPort:
                AgentLogger.log(AgentLogger.KUBERNETES, 'ReplaceTokens :: Replacing $KUBELET_STATS_PORT$ - {0}'.format(
                    KubeGlobal.kubeletStatsPort))
                data = data.replace("$KUBELET_STATS_PORT$", KubeGlobal.kubeletStatsPort)
            if "$KUBE_STATE_METRICS_URL$" in data and KubeGlobal.kubeStateMetricsUrl:
                AgentLogger.log(AgentLogger.KUBERNETES,
                                'ReplaceTokens :: Replacing $KUBE_STATE_METRICS_URL$ - {0}'.format(
                                    KubeGlobal.kubeStateMetricsUrl))
                data = data.replace("$KUBE_STATE_METRICS_URL$", KubeGlobal.kubeStateMetricsUrl)
            if "$KUBE_STATE_IP$" in data and KubeGlobal.kubeStatePodIP:
                AgentLogger.log(AgentLogger.KUBERNETES,
                                'ReplaceTokens :: Replacing $KUBE_STATE_IP$ - {0}'.format(KubeGlobal.kubeStatePodIP))
                data = data.replace("$KUBE_STATE_IP$", KubeGlobal.kubeStatePodIP)
            if "$KUBE_STATE_PORT$" in data and KubeGlobal.kubeStatePort:
                AgentLogger.log(AgentLogger.KUBERNETES,
                                'ReplaceTokens :: Replacing $KUBE_STATE_PORT$ - {0}'.format(KubeGlobal.kubeStatePort))
                data = data.replace("$KUBE_STATE_PORT$", KubeGlobal.kubeStatePort)
    except Exception as e:
        AgentLogger.log(AgentLogger.KUBERNETES, 'ReplaceTokens -> Exception -> {0}'.format(e))
    return data


def cleanup_buffer():
    try:
        cleanup_list = [KubeGlobal.WORKLOADS_PARENT_CHILD_DICT, KubeGlobal.NODES_ASSOCIATED_RES,
                        KubeGlobal.SERVICES_LABELS_LOOKUP, KubeGlobal.PODS_VS_NAMESPACE_METRICS,
                        KubeGlobal.POD_IP_VS_NAME]
        for temp_dict in cleanup_list:
            clear_and_init_dict(temp_dict)
    except Exception as e:
        traceback.print_exc()


def getKubeAPIServerEndPoint():
    AgentLogger.log(AgentLogger.KUBERNETES, 'getKubeAPIServerEndPoint')
    try:
        url = "/api/v1/namespaces"
        # using KubeGlobal.kubeServer 
        server = KubeGlobal.kubeServer
        server = server.replace('"', '')
        AgentLogger.log(AgentLogger.KUBERNETES, 'kubeServer - {0}'.format(server))
        status, valDict = curl_api_with_token(server + url)
        if status == 200:
            AgentLogger.log(AgentLogger.KUBERNETES, 'KubeGlobal.kubeServer - 200')
            return server

        # using KubeGlobal.host
        AgentLogger.log(AgentLogger.KUBERNETES, 'host - {0}'.format(KubeGlobal.host))
        status, valDict = curl_api_with_token(KubeGlobal.host + url)
        if status == 200:
            AgentLogger.log(AgentLogger.KUBERNETES, 'KubeGlobal.host - 200')
            return KubeGlobal.host

    except Exception as e:
        AgentLogger.log(AgentLogger.KUBERNETES, 'getKubeAPIServerEndPoint -> Exception -> {0}'.format(e))


def shift_data_to_k8s_node(perfData):
    try:
        if "workloads" in perfData:
            perfData["kubernetes"] = perfData.pop("workloads")

        if "ComponentStatuses" in perfData:
            perfData["kubernetes"]["ComponentStatuses"] = perfData.pop("ComponentStatuses")

        if "Namespaces" in perfData:
            perfData["kubernetes"]["Namespaces"] = perfData.pop("Namespaces")
    except Exception as e:
        traceback.print_exc()
