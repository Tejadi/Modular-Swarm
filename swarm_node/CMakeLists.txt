#
# Reconfigurable swarm module — OpenThread + CoAP swarm overlay.
#
# SPDX-License-Identifier: LicenseRef-Nordic-5-Clause
#

cmake_minimum_required(VERSION 3.20.0)

find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})

project(swarm_node)

FILE(GLOB app_sources src/*.c)
target_sources(app PRIVATE ${app_sources})

# The shared wire-protocol header lives one level up so the host-side Python
# codec (proto/swarm_proto.py) and this firmware stay in lockstep.
target_include_directories(app PRIVATE src ${CMAKE_CURRENT_SOURCE_DIR}/../proto)
