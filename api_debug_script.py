import requests
import os
import json
import argparse

TOKEN = None
HOST=None
K8s_ENDPOINTS = {
    'replicasets':'/apis/apps/v1/replicasets',
    'pods':'/api/v1/pods',
    'nodes':'/api/v1/nodes',
    'namespaces':'/api/v1/namespaces',
    'endpoints':'/api/v1/endpoints',
    'componentstatuses':'/api/v1/componentstatuses',
    'hpa':'/apis/autoscaling/v1/horizontalpodautoscalers',
    'daemonsets':'/apis/apps/v1/daemonsets',
    'services':'/api/v1/services',
    'deployments':'/apis/apps/v1/deployments',
    'statefulsets':'/apis/apps/v1/statefulsets',
    'pv':'/api/v1/persistentvolumes',
    'jobs':'/apis/batch/v1/jobs',
    'ingress':'/apis/networking.k8s.io/v1/ingresses',
    'summary':'/stats/summary',
    'cadvisor':'/metrics/cadvisor'
}

def get_token():
    global TOKEN
    tokenFile = "/var/run/secrets/kubernetes.io/serviceaccount/token"
    if os.path.isfile(tokenFile):
        file_obj = open(tokenFile, "r")
        kubeToken = file_obj.read()
        kubeToken = kubeToken.rstrip()
        if kubeToken:
            TOKEN=kubeToken

def test_api_endpoint(endpoint):
    try:
        get_token()
        headers = {'Authorization' : 'Bearer ' + TOKEN}
        data = requests.get(HOST, headers=headers, verify=False, timeout=20)
        data = data.content
        if isinstance(data, bytes):
            data = data.decode()
        write_obj = open(endpoint.split('/')[-1]+'.json', 'a')
        write_obj.write(data)
        write_obj.close()
    except Exception as e:
        print(e)

def decide_endpoint(endpoint):
    global HOST
    if endpoint == 'cadvisor' or endpoint == 'summary':
        HOST='https://'+os.getenv('NODE_IP')+':10250'
    else: HOST='https://kubernetes.default'

    HOST = HOST + K8s_ENDPOINTS[endpoint]

if __name__=='__main__':
    print(K8s_ENDPOINTS)
    while True:
        try:
            endpoint = str(input())
            decide_endpoint(endpoint)
            test_api_endpoint(endpoint)
        except Exception as e:
            print(e)
