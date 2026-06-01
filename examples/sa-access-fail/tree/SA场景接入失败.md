# SA场景接入失败
## RRC建立失败
N.RRC.SetupAtt.Sum - N.RRC.SetupSucc.Sum > 0
### 无线资源不足
N.RRC.SetupFail.NoRes > 0
### 接纳拒绝
DCErrType 中 RrcReject 占比 > 30%
## NG接口建立失败
### AMF无响应
N.NG.Setup.Fail.NoRsp > 0
### 传输中断
DCErrType 中 TransLink 占比 > 20%
