#!/usr/bin/python
# Copyright (C) 2006-2007 XenSource Ltd.
# Copyright (C) 2008-2009 Citrix Ltd.
#
# This program is free software; you can redistribute it and/or modify 
# it under the terms of the GNU Lesser General Public License as published 
# by the Free Software Foundation; version 2.1 only.
#
# This program is distributed in the hope that it will be useful, 
# but WITHOUT ANY WARRANTY; without even the implied warranty of 
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the 
# GNU Lesser General Public License for more details.
#
# Miscellaneous LVM utility functions
#


import re
import os
import errno
import time

import SR
import util
import xs_errors
import xml.dom.minidom
from lvhdutil import VG_LOCATION,VG_PREFIX
import lvmcache
import srmetadata
import vhdutil

MDVOLUME_NAME = 'MGT'
VDI_UUID_TAG_PREFIX = 'vdi_'
LVM_BIN = "/usr/sbin"
CMD_VGS       = os.path.join(LVM_BIN, "vgs")
CMD_VGCREATE  = os.path.join(LVM_BIN, "vgcreate")
CMD_VGREMOVE  = os.path.join(LVM_BIN, "vgremove")
CMD_VGCHANGE  = os.path.join(LVM_BIN, "vgchange")
CMD_VGEXTEND  = os.path.join(LVM_BIN, "vgextend")
CMD_PVS       = os.path.join(LVM_BIN, "pvs")
CMD_PVCREATE  = os.path.join(LVM_BIN, "pvcreate")
CMD_PVREMOVE  = os.path.join(LVM_BIN, "pvremove")
CMD_PVRESIZE  = os.path.join(LVM_BIN, "pvresize")
CMD_LVS       = os.path.join(LVM_BIN, "lvs")
CMD_LVDISPLAY = os.path.join(LVM_BIN, "lvdisplay")
CMD_LVCREATE  = os.path.join(LVM_BIN, "lvcreate")
CMD_LVREMOVE  = os.path.join(LVM_BIN, "lvremove")
CMD_LVCHANGE  = os.path.join(LVM_BIN, "lvchange")
CMD_LVRENAME  = os.path.join(LVM_BIN, "lvrename")
CMD_LVRESIZE  = os.path.join(LVM_BIN, "lvresize")
CMD_DMSETUP   = "/sbin/dmsetup"

LVM_SIZE_INCREMENT = 4 * 1024 * 1024
LV_TAG_HIDDEN = "hidden"
LVM_FAIL_RETRIES = 10
METADATA_UPDATE_OBJECT_TYPE_TAG = 'objtype'
METADATA_OBJECT_TYPE_SR = 'sr'
METADATA_OBJECT_TYPE_VDI = 'vdi'

class LVInfo:
    name = ""
    size = 0
    active = False
    open = False
    hidden = False
    readonly = False

    def __init__(self, name):
        self.name = name

    def toString(self):
        return "%s, size=%d, active=%s, open=%s, hidden=%s, ro=%s" % \
                (self.name, self.size, self.active, self.open, self.hidden, \
                self.readonly)


def _checkVG(vgname):
    try:
        cmd = [CMD_VGS, vgname]
        util.pread2(cmd)
        return True
    except:
        return False

def _checkPV(pvname):
    try:
        cmd = [CMD_PVS, pvname]
        util.pread2(cmd)
        return True
    except:
        return False

def _checkLV(path):
    try:
        cmd = [CMD_LVDISPLAY, path]
        util.pread2(cmd)
        return True
    except:
        return False

def _getLVsize(path):
    try:
        cmd = [CMD_LVDISPLAY, "-c", path]
        lines = util.pread2(cmd).split(':')
        return long(lines[6]) * 512
    except:
        raise xs_errors.XenError('VDIUnavailable', \
              opterr='no such VDI %s' % path)

def _getVGstats(vgname):
    try:
        cmd = [CMD_VGS, "--noheadings", "--units", "b", vgname]
        text = util.pread(cmd).split()
        size = long(text[5].replace("B",""))
        utilisation = size - long(text[6].replace("B",""))
        freespace = size - utilisation
        stats = {}
        stats['physical_size'] = size
        stats['physical_utilisation'] = utilisation
        stats['freespace'] = freespace
        return stats
    except util.CommandException, inst:
        raise xs_errors.XenError('VDILoad', \
              opterr='rvgstats failed error is %d' % inst.code)
    except ValueError:
        raise xs_errors.XenError('VDILoad', opterr='rvgstats failed')

def _getPVname(pvname, prefix_list):
    try:
        cmd = [CMD_PVS, "--noheadings", "-o", "vg_name", pvname]
        return match_VG(util.pread2(cmd), prefix_list)
    except:
        return ""

def match_VG(s, prefix_list):
    for val in prefix_list:
        regex = re.compile(val)
        if regex.search(s, 0):
            return s.split(val)[1]
    return ""

def scan_srlist(prefix, root):
    VGs = {}
    for dev in root.split(','):
        try:
            val = _getPVname(dev, [prefix]).strip('\n')
            if len(val):
                if VGs.has_key(val):
                    VGs[val] += ",%s" % dev
                else:
                    VGs[val] = dev
        except:
            continue
    return VGs

def srlist_toxml(VGs, includeMetadata = False):
    dom = xml.dom.minidom.Document()
    element = dom.createElement("SRlist")
    dom.appendChild(element)
        
    for val in VGs:
        entry = dom.createElement('SR')
        element.appendChild(entry)

        subentry = dom.createElement("UUID")
        entry.appendChild(subentry)
        textnode = dom.createTextNode(val)
        subentry.appendChild(textnode)

        subentry = dom.createElement("Devlist")
        entry.appendChild(subentry)
        textnode = dom.createTextNode(VGs[val])
        subentry.appendChild(textnode)
        
        if includeMetadata:
            metadataVDI = None
            lvmCache = lvmcache.LVMCache(VG_PREFIX + val)
            
            # add SR name_label
            sr_metadata = getMetadata(VG_PREFIX + val, lvmCache, MDVOLUME_NAME)
            subentry = dom.createElement("name_label")
            entry.appendChild(subentry)
            textnode = dom.createTextNode(sr_metadata[srmetadata.NAME_LABEL_TAG])
            subentry.appendChild(textnode)
            
            # add SR description            
            subentry = dom.createElement("name_description")
            entry.appendChild(subentry)
            textnode = dom.createTextNode(sr_metadata[srmetadata.NAME_DESCRIPTION_TAG])
            subentry.appendChild(textnode)
            
            # add metadata VDI UUID
            metadataVDI = findMetadataVDI(VG_PREFIX + val, lvmCache, MDVOLUME_NAME)
            subentry = dom.createElement("pool_metadata_detected")
            entry.appendChild(subentry)
            if metadataVDI != None:
                subentry.appendChild(dom.createTextNode("true"))
            else:
                subentry.appendChild(dom.createTextNode("false"))
                
    return dom.toprettyxml()

def createVG(root, vgname):
    systemroot = util.getrootdev()
    rootdev = root.split(',')[0]

    # Create PVs for each device
    for dev in root.split(','):
        if dev in [systemroot, '%s1' % systemroot, '%s2' % systemroot]:
            raise xs_errors.XenError('Rootdev', \
                  opterr=('Device %s contains core system files, ' \
                          + 'please use another device') % dev)
        if not os.path.exists(dev):
            raise xs_errors.XenError('InvalidDev', \
                  opterr=('Device %s does not exist') % dev)

        try:
            f = os.open("%s" % dev, os.O_RDWR | os.O_EXCL)
        except:
            raise xs_errors.XenError('SRInUse', \
                  opterr=('Device %s in use, please check your existing ' \
                  + 'SRs for an instance of this device') % dev)
        os.close(f)
        try:
            # Overwrite the disk header, try direct IO first
            cmd = [util.CMD_DD, "if=/dev/zero", "of=%s" % dev, "bs=1M",
                    "count=100", "oflag=direct"]
            util.pread2(cmd)
        except util.CommandException, inst:
            if inst.code == errno.EPERM:
                try:
                    # Overwrite the disk header, try normal IO
                    cmd = [util.CMD_DD, "if=/dev/zero", "of=%s" % dev,
                            "bs=1M", "count=100"]
                    util.pread2(cmd)
                except util.CommandException, inst:
                    raise xs_errors.XenError('LVMWrite', \
                          opterr='device %s' % dev)
            else:
                raise xs_errors.XenError('LVMWrite', \
                      opterr='device %s' % dev)
        try:
            cmd = [CMD_PVCREATE, "--metadatasize", "10M", dev]
            util.pread2(cmd)
        except util.CommandException, inst:
            raise xs_errors.XenError('LVMPartCreate', \
                  opterr='error is %d' % inst.code)

    # Create VG on first device
    try:
        cmd = [CMD_VGCREATE, vgname, rootdev]
        util.pread2(cmd)
    except :
        raise xs_errors.XenError('LVMGroupCreate')

    # Then add any additional devs into the VG
    for dev in root.split(',')[1:]:
        try:
            cmd = [CMD_VGEXTEND, vgname, dev]
            util.pread2(cmd)
        except util.CommandException, inst:
            # One of the PV args failed, delete SR
            try:
                cmd = [CMD_VGREMOVE, vgname]
                util.pread2(cmd)
            except:
                pass
            raise xs_errors.XenError('LVMGroupCreate')
    try:
        cmd = [CMD_VGCHANGE, "-an", "--master", vgname]
        util.pread2(cmd)
    except util.CommandException, inst:
        raise xs_errors.XenError('LVMUnMount', \
              opterr='errno is %d' % inst.code)

def removeVG(root, vgname):
    # Check PVs match VG
    try:
        for dev in root.split(','):
            cmd = [CMD_PVS, dev]
            txt = util.pread2(cmd)
            if txt.find(vgname) == -1:
                raise xs_errors.XenError('LVMNoVolume', \
                      opterr='volume is %s' % vgname)
    except util.CommandException, inst:
        raise xs_errors.XenError('PVSfailed', \
              opterr='error is %d' % inst.code)

    try:
        cmd = [CMD_VGREMOVE, vgname]
        util.pread2(cmd)

        for dev in root.split(','):
            cmd = [CMD_PVREMOVE, dev]
            util.pread2(cmd)
    except util.CommandException, inst:
        raise xs_errors.XenError('LVMDelete', \
              opterr='errno is %d' % inst.code)

def refreshPV(dev):
    try:
        cmd = [CMD_PVRESIZE, dev]
        util.pread2(cmd)
    except util.CommandException, inst:
        util.SMlog("Failed to grow the PV, non-fatal")
    
def setActiveVG(path, active):
    "activate or deactivate VG 'path'"
    val = "n"
    if active:
        val = "y"
    cmd = [CMD_VGCHANGE, "-a" + val, "--master", path]
    text = util.pread2(cmd)

def create(name, size, vgname, tag = None, activate = True):
    size_mb = size / 1024 / 1024
    cmd = [CMD_LVCREATE, "-n", name, "-L", str(size_mb), vgname]
    if tag:
        cmd.extend(["--addtag", tag])
    if not activate:
        cmd.extend(["--inactive", "--zero=n"])
    util.pread2(cmd)

def remove(path):
    # see deactivateNoRefcount()
    for i in range(LVM_FAIL_RETRIES):
        try:
            _remove(path)
            break
        except util.CommandException, e:
            if i >= LVM_FAIL_RETRIES - 1:
                raise
            util.SMlog("*** lvremove failed on attempt #%d" % i)
    _lvmBugCleanup(path)

def _remove(path):
    cmd = [CMD_LVREMOVE, "-f", path]
    ret = util.pread2(cmd)

def rename(path, newName):
    cmd = [CMD_LVRENAME, path, newName]
    util.pread(cmd)

def setReadonly(path, readonly):
    val = "r"
    if not readonly:
        val += "w"
    cmd = [CMD_LVCHANGE, path, "-p", val]
    ret = util.pread(cmd)

#def getSize(path):
#    return _getLVsize(path)
#    #cmd = [CMD_LVS, "--noheadings", "--units", "B", path]
#    #ret = util.pread2(cmd)
#    #size = int(ret.strip().split()[-1][:-1])
#    #return size

def setSize(path, size, confirm):
    sizeMB = size / (1024 * 1024)
    cmd = [CMD_LVRESIZE, "-L", str(sizeMB), path]
    if confirm:
        util.pread3(cmd, "y\n")
    else:
        util.pread(cmd)

#def getTagged(path, tag):
#    """Return LV names of all LVs that have tag 'tag'; 'path' is either a VG
#    path or the entire LV path"""
#    tagged = []
#    cmd = [CMD_LVS, "--noheadings", "-o", "lv_name,lv_tags", path]
#    text = util.pread(cmd)
#    for line in text.split('\n'):
#        if not line:
#            continue
#        fields = line.split()
#        lvName = fields[0]
#        if len(fields) >= 2:
#            tags = fields[1]
#            if tags.find(tag) != -1:
#                tagged.append(lvName)
#    return tagged

#def getHidden(path):
#    return len(getTagged(path, LV_TAG_HIDDEN)) == 1

def setHidden(path, hidden = True):
    opt = "--addtag"
    if not hidden:
        opt = "--deltag"
    cmd = [CMD_LVCHANGE, opt, LV_TAG_HIDDEN, path]
    util.pread2(cmd)

def activateNoRefcount(path, refresh):
    cmd = [CMD_LVCHANGE, "-ay", path]
    if refresh:
        cmd.append("--refresh")
    text = util.pread2(cmd)
    if not _checkActive(path):
        raise util.CommandException(-1, str(cmd), "LV not activated")
    if refresh:
        mapperDevice = path[5:].replace("-", "--").replace("/", "-")
        cmd = [CMD_DMSETUP, "table", mapperDevice]
        ret = util.pread(cmd)
        util.SMlog("DM table for %s: %s" % (path, ret.strip()))

def deactivateNoRefcount(path):
    # LVM has a bug where if an "lvs" command happens to run at the same time 
    # as "lvchange -an", it might hold the device in use and cause "lvchange 
    # -an" to fail. Thus, we need to retry if "lvchange -an" fails. Worse yet, 
    # the race could lead to "lvchange -an" starting to deactivate (removing 
    # the symlink), failing to "dmsetup remove" the device, and still returning  
    # success. Thus, we need to check for the device mapper file existence if 
    # "lvchange -an" returns success. 
    for i in range(LVM_FAIL_RETRIES):
        try:
            _deactivate(path)
            break
        except util.CommandException:
            if i >= LVM_FAIL_RETRIES - 1:
                raise
            util.SMlog("*** lvchange -an failed on attempt #%d" % i)
    _lvmBugCleanup(path)

def _deactivate(path):
    cmd = [CMD_LVCHANGE, "-an", path]
    text = util.pread2(cmd)

#def getLVInfo(path):
#    cmd = [CMD_LVS, "--noheadings", "--units", "b", "-o", "+lv_tags", path]
#    text = util.pread2(cmd)
#    lvs = dict()
#    for line in text.split('\n'):
#        if not line:
#            continue
#        fields = line.split()
#        lvName = fields[0]
#        lvInfo = LVInfo(lvName)
#        lvInfo.size = long(fields[3].replace("B",""))
#        lvInfo.active = (fields[2][4] == 'a')
#        lvInfo.open = (fields[2][5] == 'o')
#        lvInfo.readonly = (fields[2][1] == 'r')
#        if len(fields) >= 5 and fields[4] == LV_TAG_HIDDEN:
#            lvInfo.hidden = True
#        lvs[lvName] = lvInfo
#    return lvs

def _checkActive(path):
    if util.pathexists(path):
        return True

    util.SMlog("_checkActive: %s does not exist!" % path)
    symlinkExists = os.path.lexists(path)
    util.SMlog("_checkActive: symlink exists: %s" % symlinkExists)

    mapperDeviceExists = False
    mapperDevice = path[5:].replace("-", "--").replace("/", "-")
    cmd = [CMD_DMSETUP, "status", mapperDevice]
    try:
        ret = util.pread2(cmd)
        mapperDeviceExists = True
        util.SMlog("_checkActive: %s: %s" % (mapperDevice, ret))
    except util.CommandException:
        util.SMlog("_checkActive: device %s does not exist" % mapperDevice)

    mapperPath = "/dev/mapper/" + mapperDevice
    mapperPathExists = util.pathexists(mapperPath)
    util.SMlog("_checkActive: path %s exists: %s" % \
            (mapperPath, mapperPathExists))

    if mapperDeviceExists and mapperPathExists and not symlinkExists:
        # we can fix this situation manually here
        os.symlink(mapperPath, path)
        if util.pathexists(path):
            util.SMlog("_checkActive: created the symlink manually")
            return True
        else:
            util.SMlog("ERROR: failed to symlink!")

    return False

def _lvmBugCleanup(path):
    # the device should not exist at this point. If it does, this was an LVM 
    # bug, and we manually clean up after LVM here
    mapperDevice = path[5:].replace("-", "--").replace("/", "-")
    mapperPath = "/dev/mapper/" + mapperDevice
            
    nodeExists = False
    cmd = [CMD_DMSETUP, "status", mapperDevice]
    try:
        util.pread(cmd, expect_rc=1)
    except util.CommandException, e:
        if e.code == 0:
            nodeExists = True

    if not util.pathexists(mapperPath) and not nodeExists:
        return

    util.SMlog("_lvmBugCleanup: seeing dm file %s" % mapperPath)

    # destroy the dm device
    if nodeExists:
        util.SMlog("_lvmBugCleanup: removing dm device %s" % mapperDevice)
        cmd = [CMD_DMSETUP, "remove", mapperDevice]
        for i in range(LVM_FAIL_RETRIES):
            try:
                util.pread2(cmd)
                break
            except util.CommandException, e:
                if i < LVM_FAIL_RETRIES - 1:
                    util.SMlog("Failed on try %d, retrying" % i)
                    time.sleep(1)
                else:
                    # make sure the symlink is still there for consistency
                    if not os.path.lexists(path):
                        os.symlink(mapperPath, path)
                        util.SMlog("_lvmBugCleanup: restored symlink %s" % path)
                    raise e

    if util.pathexists(mapperPath):
        os.unlink(mapperPath)
        util.SMlog("_lvmBugCleanup: deleted devmapper file %s" % mapperPath)

    # delete the symlink
    if os.path.lexists(path):
        os.unlink(path)
        util.SMlog("_lvmBugCleanup: deleted symlink %s" % path)

def getMetadata(vgName, lvmCache, mgtLVName):
    try:
        try:
            mgtLVPath = os.path.join(VG_LOCATION, vgName)
            mgtLVPath = os.path.join(mgtLVPath, mgtLVName)        
            lvmCache.activateNoRefcount(mgtLVName)
            try:
                Dict = srmetadata.getMetadata(mgtLVPath)
            except:
                # Maybe there is no metadata yet
                Dict = {}                            
            
        except Exception, e:
            util.SMlog('Exception getting metadata. Error: %s' % str(e))
            raise xs_errors.XenError('MetadataError', \
                         opterr='%s' % str(e))
    finally:
        lvmCache.deactivateNoRefcount(mgtLVName)
        
    return Dict

def writeMetadata(vgName, lvmCache, mgtLVName, Dict):
    try:
        try:
            mgtLVPath = os.path.join(VG_LOCATION, vgName)
            mgtLVPath = os.path.join(mgtLVPath, mgtLVName)        
            lvmCache.activateNoRefcount(mgtLVName)
            srmetadata.writeMetadata(mgtLVPath, Dict)        
        except Exception, e:
            util.SMlog('Exception writing metadata. Error: %s' % str(e))
            raise xs_errors.XenError('MetadataError', \
                         opterr='%s' % str(e))
    finally:
        lvmCache.deactivateNoRefcount(mgtLVName)    
    
# read metadata for this SR and find if a metadata VDI exists 
def findMetadataVDI(vgName, lvmCache, mgtLVName):
    util.SMlog("Checking if metadata for VG %s contains a metadata VDI" % vgName)        
    try:
        Dict = getMetadata(vgName, lvmCache, mgtLVName)
        for key in Dict.keys():
            if util.exactmatch_uuid(key):
                if Dict[key]['type'] == 'metadata' and \
                Dict[key]['is_a_snapshot'] == '0':
                    return key        
        
        return None
    except Exception, e:
        util.SMlog('Checking if SR metadata for at %s contains a metadata VDI. \
                   Error: %s' % (mgtLVPath, str(e)))
        raise xs_errors.XenError('MetadataError', \
                     opterr='%s' % str(e))        
        
# update the SR information or one of the VDIs information
# the passed in map would have a key 'objtype', either sr or vdi.
# if the key is sr, the following might be passed in
#   SR name-label
#   SR name_description
# if the key is vdi, the following information per VDI may be passed in
#   uuid - mandatory
#   name-label
#   name_description
#   is_a_snapshot
#   snapshot_of, if snapshot status is true
#   snapshot time
#   type: system, user or metadata etc
#   vdi_type: raw or vhd
#   read_only
#   location
#   managed
#   metadata_of_pool
def updateMetadata(vgName, lvmCache, mgtLVName, update_map = {}):        
    util.SMlog("Updating metadata : %s" % update_map)
    try:
        mgtLVPath = os.path.join(VG_LOCATION, vgName)
        mgtLVPath = os.path.join(mgtLVPath, mgtLVName)    
        lvmCache.activateNoRefcount(mgtLVName)
        try:
            objtype = update_map[METADATA_UPDATE_OBJECT_TYPE_TAG]
            del update_map[METADATA_UPDATE_OBJECT_TYPE_TAG]
            
            if objtype == METADATA_OBJECT_TYPE_SR:
                srmetadata.updateSR(mgtLVPath, update_map)
            elif objtype == METADATA_OBJECT_TYPE_VDI: 
                srmetadata.updateVdi(mgtLVPath, update_map)
        except Exception, e:
            util.SMlog('Error updating Metadata Volume with update' \
                         'map: %s. Error: %s' % (update_map, str(e)))
            raise xs_errors.XenError('MetadataError', \
                         opterr='%s' % str(e))    
    finally:
        lvmCache.deactivateNoRefcount(mgtLVName)
        
def deleteVdiFromMetadata(vgName, lvmCache, mgtLVName, vdi_uuid):        
    util.SMlog("Deleting vdi: %s" % vdi_uuid)
    try:
        mgtLVPath = os.path.join(VG_LOCATION, vgName)
        mgtLVPath = os.path.join(mgtLVPath, mgtLVName)    
        lvmCache.activateNoRefcount(mgtLVName)
        try:
            srmetadata.deleteVdi(mgtLVPath, vdi_uuid)
        except Exception, e:
            util.SMlog('Error deleting vdi %s from the metadata. \
                         Error: %s' % (vdi_uuid, str(e)))
            raise xs_errors.XenError('MetadataError', \
                         opterr='%s' % str(e))
    finally:
        lvmCache.deactivateNoRefcount(mgtLVName)

def addVdi(vgName, lvmCache, mgtLVName, vdi_info = {}):    
    util.SMlog("Adding VDI with info: %s" % vdi_info)
    try:
        mgtLVPath = os.path.join(VG_LOCATION, vgName)
        mgtLVPath = os.path.join(mgtLVPath, mgtLVName)    
        lvmCache.activateNoRefcount(mgtLVName)
        try:                    
            srmetadata.addVdi(mgtLVPath, vdi_info)                
            
        except Exception, e:
            util.SMlog('Error adding VDI to Metadata Volume with \
                         update map: %s. Error: %s' % (vdi_info, str(e)))
            raise xs_errors.XenError('MetadataError', \
                         opterr='%s' % (str(e)))    
    finally:
        lvmCache.deactivateNoRefcount(mgtLVName)
        
def isSpaceAvailableForMetadata(vgName, lvmCache, mgtLVName, count):    
    util.SMlog("Checking if there is space in the metadata for %d VDI." % \
               count)
    try:
        mgtLVPath = os.path.join(VG_LOCATION, vgName)
        mgtLVPath = os.path.join(mgtLVPath, mgtLVName)    
        lvmCache.activateNoRefcount(mgtLVName)
        try:                    
            srmetadata.spaceAvailableForVdis(mgtLVPath, count)                            
        except Exception, e:
            raise xs_errors.XenError('MetadataError', \
                            opterr='%s' % str(e))            
    finally:
        lvmCache.deactivateNoRefcount(mgtLVName)
        