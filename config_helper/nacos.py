import asyncio
import ujson as json
import aioquic
import httpx

from sanic.log import logger
from aioquic.asyncio.client import connect
from aioquic.quic.configuration import QuicConfiguration
from aioquic.h3.connection import H3_ALPN
from aioquic.quic.logger import QuicFileLogger
from typing import Optional, Union, cast
from sanic_ext import Extend, Extension
from sanic import Sanic

from utils import get_local_ip
from http3_helper.aioquic import HttpClient, get_session_ticket, perform_http_request, save_session_ticket
from config import setting


class NacosClient:
    CONFIG_BASE_URL = '''/nacos/v1/cs/configs'''
    INSTANCE_BASE_URL = '''/nacos/v1/ns/instance'''
    SERVICE_BASE_URL = '''/nacos/v1/ns/service'''
    NAMESPACE_BASE_URL = '''/nacos/v1/console/namespaces'''

    def __init__(self, ssl=setting.NACOS_SSL):
        # TODO: aioquic 替换httpx 需nginx支持
        self.log = logger
        self.base_host = ''
        self.ssl = ssl

    def __responseHa(self, res):
        if self.ssl:
            # 支持https则使用http3请求
            self.log.info(f"响应信息：{res[0]}")
            try:
                ret = res[0]
                time = res[-1]
            except Exception as e:
                ret = res[0]
                time = -1
            return ret, time
        else:
            self.log.info(f"响应信息：{res.text}")
            try:
                ret = res.json()
                time = res.elapsed.total_seconds()
            except Exception as e:
                ret = res.text
                time = -1
            return ret, time

    async def call_api(self, data, session=None, timeout=30):
        self.log.info("call_api接受的参数data是： %s" % data)
        if self.ssl:
            # 支持https则使用http3请求
            try:
                config = QuicConfiguration(
                    is_client=True,
                    alpn_protocols=H3_ALPN,
                    quic_logger=QuicFileLogger(setting.HTTP3_CLIENT_LOG_DIR),
                )
                get_session_ticket(config)
                config.load_verify_locations(setting.CA_CERTS)
                async with connect(
                    host='https://' + setting.NACOS_HOST,
                    port=int(setting.NACOS_PORT),
                    configuration=config,
                    create_protocol=HttpClient,
                    session_ticket_handler=save_session_ticket,
                    local_port=setting.HTTP3_LOCAL_PORT,
                ) as session:
                    client = cast(HttpClient, session)
                    response = await perform_http_request(client, **data)
            except Exception as e:
                self.log.info("调用接口发生异常 ： %s" % e)
                return False, -1
            else:
                self.log.info("接口返回的消息体是： %s" % [r.data for r in response[0]])
                return self.__responseHa(res=response)
        else:
            try:
                async with httpx.AsyncClient(base_url=f"{'http://' if not self.ssl else 'https://'}{setting.NACOS_HOST}:{setting.NACOS_PORT}") as session:
                    data["timeout"] = timeout
                    response = await session.request(**data)
            except Exception as e:
                self.log.info("调用接口发生异常 ： %s" % e)
                return False, -1
            else:
                self.log.info("接口返回的消息体是： %s" % response.content)
                return self.__responseHa(res=response)

    async def get_config(
        self,
        dataId: str,
        group: str,
        tenant: Optional[str] = None
    ):
        """
        获取配置
        :param dataId: 配置的唯一标识
        :param group: 配置的分组
        :param tenant: 租户
        :return: 配置值
        err code:
        400	Bad Request	客户端请求中的语法错误
        403	Forbidden	没有权限
        404	Not Found	无法找到资源
        500	Internal Server Error	服务器内部错误
        200	OK	正常
        """
        self.log.info(
            f"获取配置的参数是： id:{dataId},group:{group}"
            f"{',tenant:' + tenant if tenant else ''}"
        )
        data = {
            "params": {
                "dataId": dataId,
                "group": group
            },
            "method": "GET",
            "url": self.base_host + self.CONFIG_BASE_URL
        }
        if tenant:
            data["params"]["tenant"] = tenant
        return await self.call_api(data=data)

    async def listener_config(
        self,
        dataId: str,
        group: str,
        contentMD5: str,
        timeout: int = 30000,
        tenant: Optional[str] = None
    ):
        """
        获取配置
        :param dataId: 配置的唯一标识
        :param group: 配置的分组
        :param contentMD5: 配置的md5值
        :param tenant: 租户
        :return: 配置值
        err code:
        400	Bad Request	客户端请求中的语法错误
        403	Forbidden	没有权限
        404	Not Found	无法找到资源
        500	Internal Server Error	服务器内部错误
        200	OK	正常
        """
        self.log.info(
            f"获取配置的参数是： id:{dataId},group:{group}"
            f"contentMD5:{contentMD5},timeout:{timeout}"
            f"{',tenant:' + tenant if tenant else ''}"
        )
        data = {
            "data": {
                "Listening-Configs":
                    f"{dataId}^2{group}^2{contentMD5}"
                    f"{'^2' + tenant + '^1' if tenant else '^1'}"
            },
            "headers": {"Long-Pulling-Timeout": timeout},
            "method": "POST",
            "url": self.base_host + self.CONFIG_BASE_URL + '/listener'
        }
        return await self.call_api(data=data)

    async def publish_config(
        self,
        dataId: str,
        group: str,
        content: Union[str, dict],
        tenant: Optional[str] = None,
        type: str = "json",
    ):
        """
        发布配置
        :param dataId: 配置的唯一标识
        :param group: 配置的分组
        :param content: 配置的内容
        :param tenant: 租户
        :param type: 配置的类型
        :return: 配置值
        err code:
        400	Bad Request	客户端请求中的语法错误
        403	Forbidden	没有权限
        404	Not Found	无法找到资源
        500	Internal Server Error	服务器内部错误
        200	OK	正常
        """
        self.log.info(
            f"发布配置的参数是： id:{dataId},group:{group},content:{content}"
            f"{',tenant:' + tenant if tenant else ''}"
        )
        data = {
            "data": {
                "dataId": dataId,
                "group": group,
                "content": content if isinstance(content, str) else json.dumps(content)
            },
            "method": "POST",
            "url": self.base_host + self.CONFIG_BASE_URL
        }
        if tenant:
            data["data"]["tenant"] = tenant
        if not isinstance(content, str):
            data["data"]['type'] = type
        return await self.call_api(data=data)

    async def delete_config(
        self,
        dataId: str,
        group: str,
        tenant: Optional[str] = None
    ):
        """
        删除配置
        :param dataId: 配置的唯一标识
        :param group: 配置的分组
        :param tenant: 租户
        :return: 配置值
        err code:
        400	Bad Request	客户端请求中的语法错误
        403	Forbidden	没有权限
        404	Not Found	无法找到资源
        500	Internal Server Error	服务器内部错误
        200	OK	正常
        """
        self.log.info(
            f"删除配置的参数是： id:{dataId},group:{group}"
            f"{',tenant:' + tenant if tenant else ''}"
        )
        data = {
            "params": {
                "dataId": dataId,
                "group": group
            },
            "method": "DELETE",
            "url": self.base_host + self.CONFIG_BASE_URL
        }
        if tenant:
            data["params"]["tenant"] = tenant
        return await self.call_api(data=data)

    async def register_instance(
        self,
        ip: str,
        port: int,
        serviceName: str,
        namespaceId: Optional[str] = None,
        weight: Optional[float] = None,
        enabled: Optional[bool] = None,
        healthy: Optional[bool] = None,
        metadata: Optional[str] = None,
        clusterName: Optional[str] = None,
        groupName: Optional[str] = None,
        ephemeral: bool = False,
    ):
        """
        注册实例
        :param appId: 应用id
        :param ip: 实例ip
        :param port: 实例端口
        :param serviceName: 实例名称
        :param namespaceId: 命名空间id
        :param weight: 实例权重
        :param enabled: 实例是否可用
        :param healthy: 实例是否健康
        :param metadata: 实例的元数据
        :param clusterName: 实例所属集群名称
        :param groupName: 实例所属分组名称
        :param ephemeral: 是否临时实例
        :return: 配置值
        err code:
        400	Bad Request	客户端请求中的语法错误
        403	Forbidden	没有权限
        404	Not Found	无法找到资源
        500	Internal Server Error	服务器内部错误
        200	OK	正常
        """
        self.log.info(
            f"注册实例的参数是： serviceName:{serviceName},ip:{ip},port:{port}"
            f"',ephemeral:{ephemeral}"
            f"{',namespaceId:' + namespaceId if namespaceId else ''}"
            f"{',weight:' + str(weight) if weight else ''}"
            f"{',enabled:' + str(enabled) if enabled else ''}"
            f"{',healthy:' + str(healthy) if healthy else ''}"
            f"{',metadata:' + metadata if metadata else ''}"
            f"{',clusterName:' + clusterName if clusterName else ''}"
            f"{',groupName:' + groupName if groupName else ''}"

        )
        data = {
            "data": {
                "ip": ip,
                "port": port,
                "serviceName": serviceName,
                "ephemeral": ephemeral
            },
            "method": "POST",
            "url": self.base_host + self.INSTANCE_BASE_URL
        }
        if namespaceId:
            data["data"]["namespaceId"] = namespaceId
        if weight:
            data["data"]["weight"] = weight
        if enabled:
            data["data"]["enabled"] = enabled
        if healthy:
            data["data"]["healthy"] = healthy
        if metadata:
            data["data"]["metadata"] = metadata
        if clusterName:
            data["data"]["clusterName"] = clusterName
        if groupName:
            data["data"]["groupName"] = groupName
        return await self.call_api(data=data)

    async def cancellation_instance(
        self,
        serviceName: str,
        ip: str,
        port: int,
        namespaceId: Optional[str] = None,
        groupName: Optional[str] = None,
        clusterName: Optional[str] = None,
        ephemeral: Optional[bool] = None,
    ):
        """
        取消注册实例
        :param serviceName: 应用名称
        :param ip: 实例ip
        :param port: 实例端口
        :param namespaceId: 命名空间id
        :param groupName: 实例所属分组名称
        :param clusterName: 实例所属集群名称
        :param ephemeral: 是否临时实例
        :return: 配置值
        err code:
        400	Bad Request	客户端请求中的语法错误
        403	Forbidden	没有权限
        404	Not Found	无法找到资源
        500	Internal Server Error	服务器内部错误
        200	OK	正常
        """
        self.log.info(
            f"取消注册实例的参数是： serviceName:{serviceName},ip:{ip},port:{port}"
            f"{',namespaceId:' + namespaceId if namespaceId else ''}"
            f"{',groupName:' + groupName if groupName else ''}"
            f"{',clusterName:' + clusterName if clusterName else ''}"
            f"{',ephemeral:' + str(ephemeral) if ephemeral else ''}"
        )
        data = {
            "params": {
                "serviceName": serviceName,
                "ip": ip,
                "port": port
            },
            "method": "DELETE",
            "url": self.base_host + self.INSTANCE_BASE_URL
        }
        if namespaceId:
            data["params"]["namespaceId"] = namespaceId
        if groupName:
            data["params"]["groupName"] = groupName
        if clusterName:
            data["params"]["clusterName"] = clusterName
        if ephemeral:
            data["params"]["ephemeral"] = ephemeral
        return await self.call_api(data=data)

    async def update_instance(
        self,
        serviceName: str,
        ip: str,
        port: int,
        namespaceId: Optional[str] = None,
        weight: Optional[float] = None,
        enabled: Optional[bool] = None,
        metadata: Optional[str] = None,
        clusterName: Optional[str] = None,
        groupName: Optional[str] = None,
        ephemeral: Optional[bool] = None,
    ):
        """
        更新实例
        :param serviceName: 应用名称
        :param ip: 实例ip
        :param port: 实例端口
        :param namespaceId: 命名空间id
        :param weight: 实例权重
        :param enabled: 实例是否可用
        :param metadata: 实例的元数据
        :param clusterName: 实例所属集群名称
        :param groupName: 实例所属分组名称
        :param ephemeral: 是否临时实例
        :return: 配置值
        err code:
        400	Bad Request	客户端请求中的语法错误
        403	Forbidden	没有权限
        404	Not Found	无法找到资源
        500	Internal Server Error	服务器内部错误
        200	OK	正常
        """
        self.log.info(
            f"更新实例的参数是： serviceName:{serviceName},ip:{ip},port:{port}"
            f"{',namespaceId:' + namespaceId if namespaceId else ''}"
            f"{',weight:' + str(weight) if weight else ''}"
            f"{',enabled:' + str(enabled) if enabled else ''}"
            f"{',metadata:' + metadata if metadata else ''}"
            f"{',clusterName:' + clusterName if clusterName else ''}"
            f"{',groupName:' + groupName if groupName else ''}"
            f"{',ephemeral:' + str(ephemeral) if ephemeral else ''}"
        )
        data = {
            "params": {
                "serviceName": serviceName,
                "ip": ip,
                "port": port
            },
            "method": "PUT",
            "url": self.base_host + self.INSTANCE_BASE_URL
        }
        if namespaceId:
            data["params"]["namespaceId"] = namespaceId
        if groupName:
            data["params"]["groupName"] = groupName
        if clusterName:
            data["params"]["clusterName"] = clusterName
        if ephemeral:
            data["params"]["ephemeral"] = ephemeral
        if weight:
            data["params"]["weight"] = weight
        if enabled:
            data["params"]["enabled"] = enabled
        if metadata:
            data["params"]["metadata"] = metadata
        return await self.call_api(data=data)

    async def get_instance(
        self,
        serviceName: str,
        namespaceId: Optional[str] = None,
        clusters: Optional[str] = None,
        groupName: Optional[str] = None,
        healthyOnly: bool = False,
    ):
        """
        获取实例
        :param serviceName: 应用名称
        :param namespaceId: 命名空间id
        :param clusters: 实例所属集群名称，多个集群用逗号分隔
        :param groupName: 实例所属分组名称
        :param healthyOnly: 是否只返回健康的实例
        :return: 配置值
        err code:
        400	Bad Request	客户端请求中的语法错误
        403	Forbidden	没有权限
        404	Not Found	无法找到资源
        500	Internal Server Error	服务器内部错误
        200	OK	正常
        """
        self.log.info(
            f"获取实例的参数是： serviceName:{serviceName}"
            f"{',namespaceId:' + namespaceId if namespaceId else ''}"
            f"{',clusters:' + clusters if clusters else ''}"
            f"{',groupName:' + groupName if groupName else ''}"
            f"{',healthyOnly:' + str(healthyOnly) if healthyOnly else ''}"
        )
        data = {
            "params": {
                "serviceName": serviceName,
            },
            "method": "GET",
            "url": self.base_host + self.INSTANCE_BASE_URL + '/list'
        }
        if namespaceId:
            data["params"]["namespaceId"] = namespaceId
        if clusters:
            data["params"]["clusters"] = clusters
        if groupName:
            data["params"]["groupName"] = groupName
        if healthyOnly:
            data["params"]["healthyOnly"] = healthyOnly
        return await self.call_api(data=data)

    async def detail_instance(
        self,
        serviceName: str,
        ip: str,
        port: int,
        namespaceId: Optional[str] = None,
        clusterName: Optional[str] = None,
        groupName: Optional[str] = None,
        healthyOnly: bool = False,
        ephemeral: Optional[bool] = None,
    ):
        """
        获取实例详情
        :param serviceName: 应用名称
        :param ip: 实例ip
        :param port: 实例端口
        :param namespaceId: 命名空间id
        :param clusterName: 实例所属集群名称
        :param groupName: 实例所属分组名称
        :param healthyOnly: 是否只返回健康的实例
        :param ephemeral: 是否临时实例
        :return: 配置值
        err code:
        400	Bad Request	客户端请求中的语法错误
        403	Forbidden	没有权限
        404	Not Found	无法找到资源
        500	Internal Server Error	服务器内部错误
        200	OK	正常
        """
        self.log.info(
            f"获取实例详情的参数是： serviceName:{serviceName},ip:{ip},port:{port}"
            f"{',namespaceId:' + namespaceId if namespaceId else ''}"
            f"{',clusterName:' + clusterName if clusterName else ''}"
            f"{',groupName:' + groupName if groupName else ''}"
            f"{',healthyOnly:' + str(healthyOnly) if healthyOnly else ''}"
            f"{',ephemeral:' + str(ephemeral) if ephemeral else ''}"
        )
        data = {
            "params": {
                "serviceName": serviceName,
                "ip": ip,
                "port": port
            },
            "method": "GET",
            "url": self.base_host + self.INSTANCE_BASE_URL
        }
        if namespaceId:
            data["params"]["namespaceId"] = namespaceId
        if clusterName:
            data["params"]["clusterName"] = clusterName
        if groupName:
            data["params"]["groupName"] = groupName
        if healthyOnly:
            data["params"]["healthyOnly"] = healthyOnly
        if ephemeral:
            data["params"]["ephemeral"] = ephemeral
        return await self.call_api(data=data)

    async def send_beat(
        self,
        serviceName: str,
        beat: dict,
        groupName: Optional[str] = None,
        ephemeral: bool = False
    ):
        """
        发送心跳
        :param serviceName: 应用名称
        :param beat: 心跳信息
        :param groupName: 实例所属分组名称
        :param ephemeral: 是否临时实例
        :return: 配置值
        err code:
        400	Bad Request	客户端请求中的语法错误
        403	Forbidden	没有权限
        404	Not Found	无法找到资源
        500	Internal Server Error	服务器内部错误
        200	OK	正常
        """
        self.log.info(
            f"发送心跳的参数是： serviceName:{serviceName}"
            f",ephemeral:{ephemeral}"
            f"{',groupName:' + groupName if groupName else ''}"
        )
        data = {
            "params": {
                "serviceName": serviceName,
                "beat": json.dumps(beat),
                "ephemeral": ephemeral
            },
            "method": "PUT",
            "url": self.base_host + self.INSTANCE_BASE_URL + '/beat'
        }
        if groupName:
            data["params"]["groupName"] = groupName
        return await self.call_api(data=data)

    async def create_service(
        self,
        serviceName: str,
        namespaceId: Optional[str] = None,
        protectThreshold: Optional[float] = None,
        groupName: Optional[str] = None,
        metadata: Optional[str] = None,
        selector: Optional[dict] = None
    ):
        """
        创建服务
        :param serviceName: 服务名称
        :param namespaceId: 命名空间id
        :param protectThreshold: 保护阈值
        :param groupName: 分组名称
        :param metadata: 元数据
        :param selector: 访问策略
        :return: 配置值
        err code:
        400	Bad Request	客户端请求中的语法错误
        403	Forbidden	没有权限
        404	Not Found	无法找到资源
        500	Internal Server Error	服务器内部错误
        200	OK	正常
        """
        self.log.info(
            f"创建服务的参数是： serviceName:{serviceName}"
            f"{',namespaceId:' + namespaceId if namespaceId else ''}"
            f"{',protectThreshold:' + str(protectThreshold) if protectThreshold else ''}"
            f"{',groupName:' + groupName if groupName else ''}"
            f"{',metadata:' + metadata if metadata else ''}"
            f"{',selector:' + json.dumps(selector) if selector else ''}"
        )
        data = {
            "data": {
                "serviceName": serviceName,
            },
            "method": "POST",
            "url": self.base_host + self.SERVICE_BASE_URL
        }
        if namespaceId:
            data["data"]["namespaceId"] = namespaceId
        if protectThreshold:
            data["data"]["protectThreshold"] = protectThreshold
        if groupName:
            data["data"]["groupName"] = groupName
        if metadata:
            data["data"]["metadata"] = metadata
        if selector:
            data["data"]["selector"] = json.dumps(selector)
        return await self.call_api(data=data)

    async def delete_service(
        self,
        serviceName: str,
        namespaceId: Optional[str] = None,
        groupName: Optional[str] = None,
    ):
        """
        删除服务
        :param serviceName: 服务名称
        :param namespaceId: 命名空间id
        :param groupName: 分组名称
        :return: 配置值
        err code:
        400	Bad Request	客户端请求中的语法错误
        403	Forbidden	没有权限
        404	Not Found	无法找到资源
        500	Internal Server Error	服务器内部错误
        200	OK	正常
        """
        self.log.info(
            f"删除服务的参数是： serviceName:{serviceName}"
            f"{',namespaceId:' + namespaceId if namespaceId else ''}"
            f"{',groupName:' + groupName if groupName else ''}"
        )
        data = {
            "params": {
                "serviceName": serviceName,
            },
            "method": "DELETE",
            "url": self.base_host + self.SERVICE_BASE_URL
        }
        if namespaceId:
            data["params"]["namespaceId"] = namespaceId
        if groupName:
            data["params"]["groupName"] = groupName
        return await self.call_api(data=data)

    async def update_service(
        self,
        serviceName: str,
        namespaceId: Optional[str] = None,
        protectThreshold: Optional[float] = None,
        groupName: Optional[str] = None,
        metadata: Optional[str] = None,
        selector: Optional[dict] = None
    ):
        """
        更新服务
        :param serviceName: 服务名称
        :param namespaceId: 命名空间id
        :param protectThreshold: 保护阈值
        :param groupName: 分组名称
        :param metadata: 元数据
        :param selector: 访问策略
        :return: 配置值
        err code:
        400	Bad Request	客户端请求中的语法错误
        403	Forbidden	没有权限
        404	Not Found	无法找到资源
        500	Internal Server Error	服务器内部错误
        200	OK	正常
        """
        self.log.info(
            f"更新服务的参数是： serviceName:{serviceName}"
            f"{',namespaceId:' + namespaceId if namespaceId else ''}"
            f"{',protectThreshold:' + str(protectThreshold) if protectThreshold else ''}"
            f"{',groupName:' + groupName if groupName else ''}"
            f"{',metadata:' + metadata if metadata else ''}"
            f"{',selector:' + json.dumps(selector) if selector else ''}"
        )
        data = {
            "params": {
                "serviceName": serviceName,
            },
            "method": "PUT",
            "url": self.base_host + self.SERVICE_BASE_URL
        }
        if namespaceId:
            data["params"]["namespaceId"] = namespaceId
        if protectThreshold:
            data["params"]["protectThreshold"] = protectThreshold
        if groupName:
            data["params"]["groupName"] = groupName
        if metadata:
            data["params"]["metadata"] = metadata
        if selector:
            data["params"]["selector"] = json.dumps(selector)
        return await self.call_api(data=data)

    async def get_service(
        self,
        serviceName: str,
        namespaceId: Optional[str] = None,
        groupName: Optional[str] = None,
    ):
        """
        查询服务
        :param serviceName: 服务名称
        :param namespaceId: 命名空间id
        :param groupName: 分组名称
        :return: 配置值
        err code:
        400	Bad Request	客户端请求中的语法错误
        403	Forbidden	没有权限
        404	Not Found	无法找到资源
        500	Internal Server Error	服务器内部错误
        200	OK	正常
        """
        self.log.info(
            f"查询服务的参数是： serviceName:{serviceName}"
            f"{',namespaceId:' + namespaceId if namespaceId else ''}"
            f"{',groupName:' + groupName if groupName else ''}"
        )
        data = {
            "params": {
                "serviceName": serviceName,
            },
            "method": "GET",
            "url": self.base_host + self.SERVICE_BASE_URL
        }
        if namespaceId:
            data["params"]["namespaceId"] = namespaceId
        if groupName:
            data["params"]["groupName"] = groupName
        return await self.call_api(data=data)

    async def get_service_list(
        self,
        pageNo: int = 1,
        pageSize: int = 20,
        namespaceId: Optional[str] = None,
        groupName: Optional[str] = None,
    ):
        """
        查询服务列表
        :param pageNo: 页码
        :param pageSize: 每页数量
        :param namespaceId: 命名空间id
        :param groupName: 分组名称
        :return: 配置值
        err code:
        400	Bad Request	客户端请求中的语法错误
        403	Forbidden	没有权限
        404	Not Found	无法找到资源
        500	Internal Server Error	服务器内部错误
        200	OK	正常
        """
        self.log.info(
            f"查询服务列表的参数是： pageNo:{pageNo},pageSize:{pageSize}"
            f"{',namespaceId:' + namespaceId if namespaceId else ''}"
            f"{',groupName:' + groupName if groupName else ''}"
        )
        data = {
            "params": {
                "pageNo": pageNo,
                "pageSize": pageSize,
            },
            "method": "GET",
            "url": self.base_host + self.SERVICE_BASE_URL + '/list'
        }
        if namespaceId:
            data["params"]["namespaceId"] = namespaceId
        if groupName:
            data["params"]["groupName"] = groupName
        return await self.call_api(data=data)

    async def update_instance_health(
        self,
        serviceName: str,
        ip: str,
        port: int,
        healthy: bool,
        namespaceId: Optional[str] = None,
        groupName: Optional[str] = None,
        clusterName: Optional[str] = None
    ):
        """
        更新实例健康状态
        :param serviceName: 服务名称
        :param ip: 实例ip
        :param port: 实例端口
        :param healthy: 健康状态
        :param namespaceId: 命名空间id
        :param groupName: 分组名称
        :param clusterName: 集群名称
        :return: 配置值
        err code:
        400	Bad Request	客户端请求中的语法错误
        403	Forbidden	没有权限
        404	Not Found	无法找到资源
        500	Internal Server Error	服务器内部错误
        200	OK	正常
        """
        self.log.info(
            f"更新实例健康状态的参数是： serviceName:{serviceName},ip:{ip},port:{port},healthy:{healthy}"
            f"{',namespaceId:' + namespaceId if namespaceId else ''}"
            f"{',groupName:' + groupName if groupName else ''}"
            f"{',clusterName:' + clusterName if clusterName else ''}"
        )
        data = {
            "params": {
                "serviceName": serviceName,
                "ip": ip,
                "port": port,
                "healthy": healthy,
            },
            "method": "PUT",
            "url": self.base_host + '/nacos/v1/ns/health/instance'
        }
        if namespaceId:
            data["params"]["namespaceId"] = namespaceId
        if groupName:
            data["params"]["groupName"] = groupName
        if clusterName:
            data["params"]["clusterName"] = clusterName
        return await self.call_api(data=data)

    async def get_namespace(self) -> tuple:
        """
        查询命名空间
        :return: 配置值
        err code:
        400	Bad Request	客户端请求中的语法错误
        403	Forbidden	没有权限
        404	Not Found	无法找到资源
        500	Internal Server Error	服务器内部错误
        200	OK	正常
        """
        data = {
            "method": "GET",
            "url": self.base_host + self.NAMESPACE_BASE_URL
        }
        return await self.call_api(data=data)

    async def create_namespace(
        self,
        namespaceName: str,
        customNamespaceId: str,
        namespaceDesc: Optional[str] = None
    ):
        """
        创建命名空间
        :param namespaceName: 命名空间名称
        :param customNamespaceId: 自定义命名空间id
        :param namespaceDesc: 命名空间描述
        :return: 配置值
        err code:
        400	Bad Request	客户端请求中的语法错误
        403	Forbidden	没有权限
        404	Not Found	无法找到资源
        500	Internal Server Error	服务器内部错误
        200	OK	正常
        """
        self.log.info(
            f"创建命名空间的参数是： namespaceName:{namespaceName}"
            f",customNamespaceId:{customNamespaceId}"
            f"{',namespaceDesc:' + namespaceDesc if namespaceDesc else ''}"
        )
        data = {
            "data": {
                "namespaceName": namespaceName,
                "customNamespaceId": customNamespaceId,
            },
            "method": "POST",
            "url": self.base_host + self.NAMESPACE_BASE_URL
        }
        if namespaceDesc:
            data["data"]["namespaceDesc"] = namespaceDesc
        return await self.call_api(data=data)

    async def update_namespace(
        self,
        namespaceName: str,
        customNamespaceId: str,
        namespaceDesc: str
    ):
        """
        更新命名空间
        :param namespaceName: 命名空间名称
        :param customNamespaceId: 自定义命名空间id
        :param namespaceDesc: 命名空间描述
        :return: 配置值
        err code:
        400	Bad Request	客户端请求中的语法错误
        403	Forbidden	没有权限
        404	Not Found	无法找到资源
        500	Internal Server Error	服务器内部错误
        200	OK	正常
        """
        self.log.info(
            f"更新命名空间的参数是： namespaceName:{namespaceName}"
            f",customNamespaceId:{customNamespaceId}"
            f",namespaceDesc:{namespaceDesc}"
        )
        data = {
            "data": {
                "namespaceName": namespaceName,
                "customNamespaceId": customNamespaceId,
            },
            "method": "PUT",
            "url": self.base_host + self.NAMESPACE_BASE_URL
        }
        if namespaceDesc:
            data["params"]["namespaceDesc"] = namespaceDesc
        return await self.call_api(data=data)

    async def delete_namespace(self, namespaceId: str):
        """
        删除命名空间
        :param namespaceId: 命名空间id
        :return: 配置值
        err code:
        400	Bad Request	客户端请求中的语法错误
        403	Forbidden	没有权限
        404	Not Found	无法找到资源
        500	Internal Server Error	服务器内部错误
        200	OK	正常
        """
        self.log.info(f"删除命名空间的参数是： namespaceId:{namespaceId}")
        data = {
            "data": {
                "namespaceId": namespaceId,
            },
            "method": "DELETE",
            "url": self.base_host + self.NAMESPACE_BASE_URL
        }
        return await self.call_api(data=data)


class NacosPlugin(Extension):
    name = 'nacos'

    def startup(self, bootstrap) -> None:
        if self.included():
            self.app.before_server_start(self.set_nacos_dependency)
            self.app.before_server_start(self.create_nacose_service)
            self.app.before_server_start(self.create_nacose_config)
            self.app.before_server_stop(self.cancellation_nacos)
        return super().startup(bootstrap)

    @staticmethod
    async def create_nacose_service(app: Sanic):
        """在服务器启动时初始化nacos命名空间、服务及实例并开始后台beat
        Args:
            app (Sanic): sanic app
        Raises:
            Exception: 获取namespace错误
            Exception: 创建namespace失败
            Exception: 创建service失败
        """
        nacos_client = NacosClient()
        res = await nacos_client.get_namespace()
        namespace_exists = False
        if res[0]:
            for namespace in res[0]['data']:
                if namespace['namespace'] == app.config.NACOS_NAMESPACE:
                    namespace_exists = True
                    break
                else:
                    continue
        else:
            raise Exception("get namespace error")
        if not namespace_exists:
            res = await nacos_client.create_namespace(app.config.NACOS_NAMESPACE, app.config.NACOS_NAMESPACE, app.config.APP_NAME)
            if not res[0]:
                raise Exception("create namespace error")
        res = await nacos_client.get_service(app.config.NACOS_NAMESPACE, app.config.NACOS_SERVICENAME, groupName=app.config.NACOS_GROUP)
        if 'service not found' in res[0]:
            res = await nacos_client.create_service(app.config.NACOS_SERVICENAME, namespaceId=app.config.NACOS_NAMESPACE, groupName=app.config.NACOS_GROUP)
            if 'ok' not in res[0]:
                raise Exception("create service error")
        # await self.create_nacose_instance(nacos_client)

    async def create_nacose_instance(self, nacos_client: NacosClient):
        """启动服务创建实例
        Args:
            nacos_client (NacosClient): nacos client
        Raises:
            Exception: 创建instance失败
        """
        res = await nacos_client.register_instance(
            get_local_ip(),
            self.app.config.PORT,
            self.app.config.NACOS_SERVICENAME,
            namespaceId=self.app.config.NACOS_NAMESPACE,
            groupName=self.app.config.NACOS_GROUP,
            enabled=True, healthy=True, ephemeral=self.app.config.NACOS_EPHEMERAL
        )
        if 'ok' not in res[0]:
            res = await nacos_client.cancellation_instance(
                self.app.config.NACOS_SERVICENAME,
                get_local_ip(),
                self.app.config.PORT,
                namespaceId=self.app.config.NACOS_NAMESPACE,
                groupName=self.app.config.NACOS_GROUP,
                ephemeral=self.app.config.NACOS_EPHEMERAL
            )
            raise Exception(
                f"create instance error, cancellation instance:{res[0]}")
        await self.send_nacos_beat(
            self.app.config.NACOS_HEARTBEAT_TASK,
            self.app.config.NACOS_SERVICENAME,
            beat={"ip": get_local_ip(), "port": self.app.config.PORT},
            groupName=self.app.config.NACOS_GROUP,
            ephemeral=self.app.config.NACOS_EPHEMERAL
        )

    async def send_nacos_beat(
        self,
        task_name: str,
        serviceName: str,
        beat: dict,
        groupName: Optional[str] = None,
        ephemeral: bool = False
    ):
        """启动nacos心跳后台任务
        Args:
            serviceName (str): 服务名称
            beat (dict): 心跳信息
            groupName (str, optional): 组名称. Defaults to None.
            ephemeral (bool, optional): 是否临时实例. Defaults to None.
        """
        self.app.add_task(self.nacos_beat(serviceName, beat,
                          groupName, ephemeral), name=task_name)

    async def nacos_beat(
        self,
        serviceName: str,
        beat: dict,
        nacos_client: NacosClient,
        groupName: Optional[str] = None,
        ephemeral: bool = False
    ):
        while True:
            await nacos_client.send_beat(serviceName, beat, groupName, ephemeral)
            await asyncio.sleep(5)

    @staticmethod
    async def create_nacose_config(app: Sanic):
        """在服务器启动时将mysql及redis共享至nacos配置
        Args:
            app (Sanic): sanic app
        Raises:
            Exception: nacos配置错误
            Exception: nacos配置错误
        """
        nacos_client = NacosClient()
        res = await nacos_client.get_config('redis', app.config.NACOS_GROUP, app.config.NACOS_NAMESPACE)
        if 'config data not exist' in res[0]:
            res = await nacos_client.publish_config(
                'redis',
                app.config.NACOS_GROUP,
                {
                    "host": app.config.REDIS_HOST,
                    "port": app.config.REDIS_PORT,
                    "password": app.config.REDIS_PASSWORD
                },
                app.config.NACOS_NAMESPACE
            )
            if not res:
                raise Exception("create config error")
        res = await nacos_client.get_config('mysql', app.config.NACOS_GROUP, app.config.NACOS_NAMESPACE)
        if 'config data not exist' in res[0]:
            res = await nacos_client.publish_config(
                'mysql',
                app.config.NACOS_GROUP,
                {
                    "host": app.config.DB_HOST,
                    "port": app.config.DB_PORT,
                    "username": app.config.DB_USER,
                    "password": app.config.DB_PASSWORD,
                    "db": app.config.DB_NAME
                },
                app.config.NACOS_NAMESPACE
            )
            if not res:
                raise Exception("create config error")

    @staticmethod
    async def set_nacos_dependency(app: Sanic):
        """通过sanic-ext dependency injection 添加nacos连接
        Args:
            app (Sanic): sanic app
        """
        con_nacos = NacosClient()
        app.ext.dependency(con_nacos)

    @staticmethod
    async def cancellation_nacos(app: Sanic):
        """服务停止，注销nacos实例，取消心跳任务
        Args:
            app (Sanic): sanic app
        """
        nacos_client = NacosClient()
        res = await nacos_client.cancellation_instance(
            app.config.NACOS_SERVICENAME,
            get_local_ip(),
            app.config.PORT,
            namespaceId=app.config.NACOS_NAMESPACE,
            groupName=app.config.NACOS_GROUP,
            ephemeral=app.config.NACOS_EPHEMERAL
        )
        logger.info(f"cancellation instance:{res[0]}")
        try:
            await app.cancel_task(app.config.NACOS_HEARTBEAT_TASK)
            logger.info(f"stop 【{app.config.NACOS_HEARTBEAT_TASK}】 task.")
        except:
            ...

    def included(self):
        return self.app.config.NACOS


Extend.register(NacosPlugin)