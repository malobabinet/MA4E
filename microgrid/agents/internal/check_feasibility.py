# -*- coding: utf-8 -*-
"""
Created on Fri May 28 19:19:33 2021

@author: B57876
"""

# Check feasibility of different player loads
# ATTENTION CONVENTION ARR/DEP pour la station de charge ; dans mon code les arr/dep
# sont des pas de tps dans un modèle à tps discret ; dans les données d'entrée c'est en heures...
import numpy as np
from typing import Dict, List, Union
from microgrid.assets.battery import Battery
from microgrid.agents.data_center_agent import DataCenterAgent
from microgrid.agents.charging_station_agent import ChargingStationEnv


MSG_ERROR_TYPE_OR_SIZE = "Error type or size"
WRONG_FORMAT_SCORE = 1000
PU_INFEAS_SCORE = 0.1
DEFAULT_PU_INFEAS_SCORE = 0.01 # when 0 value is the one to be obtained, no relative deviation can be calc.
AGENT_TYPES = ["solar_farm", "data_center", "industrial", "charging_station"]
ONE_DIM_AGENTS = list(set(AGENT_TYPES) - {"charging_station"})


def check_load_profile_type_and_size(agent_type: str, load_profile: Union[np.ndarray, list], n_ts: int,
                                     n_ev: int = 4) -> Dict[str, bool]:
    """
    Check type and size of load profile of a given agent
    Args:
        agent_type: type of the agent considered, to distinguish EV charging station matricial load profile from the
        other agents 1-dim ones
        load_profile: load profile to be checked
        n_ts: number of time-slots requested
        n_ev: idem for number of EV; only needed when EV charging station is considered

    Returns:
        a dictionary with 'type' and 'size' status of the check
    """
    assert agent_type in AGENT_TYPES
    check_status = {"type": None, "size": None}
    if agent_type in ONE_DIM_AGENTS:
        if isinstance(load_profile, list):
            check_status["type"] = check_all_float_in_list(my_list=load_profile)
            check_status["size"] = len(load_profile) == n_ts
        elif isinstance(load_profile, np.ndarray):
            check_status["type"] = load_profile.dtype == float
            check_status["size"] = load_profile.shape in [(1, n_ts), (n_ts,)]
        else:
            check_status["type"] = False
    # particular case of EV charging station
    else:
        if isinstance(load_profile, list):
            n_ev_comput = len(load_profile)
            size_error = not (n_ev_comput == n_ev)
            type_error = False
            for i_ev in range(n_ev_comput):
                if not isinstance(load_profile[i_ev], list) or isinstance(load_profile[i_ev], np.ndarray):
                    type_error = True
                else:
                    if not len(load_profile[i_ev]) == n_ts:
                        type_error = True
            check_status["type"] = not type_error
            check_status["size"] = not size_error
        elif isinstance(load_profile, np.ndarray):
            check_status["type"] = True
            check_status["size"] = load_profile.shape == (n_ev, n_ts)
        else:
            check_status["type"] = False
    return check_status


def check_all_float_in_list(my_list: list) -> bool:
    return all([isinstance(my_list[t], float) for t in range(len(my_list))])


def msg_error_type_and_size(agent_type: str, n_ts: int, n_ev: int = 4) -> str:
    if agent_type in ONE_DIM_AGENTS:
        return f"Wrong type or size for {agent_type} load profile; it must be List[float] or np.array with size {n_ts}"
    else:
        return f"Wrong type or size for {agent_type} load profile; it must be List[List[float]] or List[np.array] " \
               f"or np.array with size ({n_ev}, {n_ts})"


def calculate_infeas_score(n_infeas_check: int, infeas_list: List[float], n_default_infeas: int) -> float:
    """
    Calculate infeasibility score 
    
    :param n_infeas_check: number of infeasibility check (number of constraints to be respected)
    :param infeas_list: list of infeasibility values, relatively to the NONZERO values to be respected
    :param n_default_infeas: number of infeas. corresponding to ZERO values to be respected
    """
    
    return np.sum(infeas_list) / n_infeas_check * PU_INFEAS_SCORE + n_default_infeas * DEFAULT_PU_INFEAS_SCORE


def calculate_ev_soc_trajectory(ev_init_soc: float, load_profile: np.array, charge_eff: float, discharge_eff: float,
                                ev_arrival_time: int, delta_t_s: int) -> np.array:
    """
    Calculate EV state-of-charge trajectory

    Args:
        ev_init_soc: EV initial SOC
        load_profile: load profile of this EV
        charge_eff: charging efficiency
        discharge_eff: discharging efficiency
        ev_arrival_time: time-slot index corresponding to EV arrival
        delta_t_s (int): time-slot duration, in seconds

    Returns:
        np.array: the EV SOC trajectory
    """

    ev_batt_soc = ev_init_soc + (charge_eff * np.cumsum(np.maximum(load_profile, 0))
                                 - 1 / discharge_eff * np.cumsum(np.maximum(-load_profile, 0))) * delta_t_s / 3600
    # diminish SoC when arriving at CS with E quantity consumed when driving
    ev_batt_soc[ev_arrival_time] -= 4

    return ev_batt_soc


def check_data_center_feasibility(data_center_agent: DataCenterAgent, load_profile: Union[np.ndarray, list],
                                  it_load_profile: np.ndarray) -> (Dict[str, str], float):
    """
    Check heat pump load profile obtained from the DC module

    :param data_center_agent: the DataCenterAgent object for which load profile must be checked
    :param load_profile: vector (1, n_ts) with heat pump load profile
    :param it_load_profile: IT load profile
    :return: returns a dict with all errors and the "global" infeasibility score
    """
    n_ts = data_center_agent.nbr_future_time_slots
    delta_t_s = int(data_center_agent.delta_t.total_seconds())
    check_msg = {}

    # TODO OJ: obtenir IT load directement à partir de l'objet data_center?
    # check proper IT load
    assert isinstance(it_load_profile, np.ndarray)
    assert it_load_profile.dtype == float
    assert it_load_profile.shape == (n_ts,)

    agent_type = "data_center"
    type_and_size_check = \
        check_load_profile_type_and_size(agent_type=agent_type, load_profile=load_profile, n_ts=n_ts)
    if any([check_status is False for check_status in type_and_size_check.values()]):
        print(msg_error_type_and_size(agent_type=agent_type, n_ts=n_ts))
        check_msg["format"] = MSG_ERROR_TYPE_OR_SIZE
        return check_msg, WRONG_FORMAT_SCORE

    # check that DC load is non-negative and smaller than IT load up to a proportional coeff.
    n_infeas_check = 0  # number of constraints checked (to normalize the infeas. score at the end)

    # identify time-slots with non-zero IT cons.
    nonzero_it_load_ts = np.where(it_load_profile > 0)[0]
    prop_nonzero_it_load = data_center_agent.COP_CS \
                           / (data_center_agent.EER * (data_center_agent.COP_HP - 1) * delta_t_s) \
                           * it_load_profile[nonzero_it_load_ts]
    infeas_list = list(np.maximum(load_profile[nonzero_it_load_ts] - prop_nonzero_it_load, 0) / prop_nonzero_it_load)
    n_infeas_check += len(prop_nonzero_it_load)

    n_default_infeas = 0
    # loop over ts with zero IT load
    for t in range(n_ts):
        if t not in nonzero_it_load_ts and load_profile[t] > 0:
            n_default_infeas += 1

    # Check that HP load prof. be non-negative
    n_default_infeas += len(np.where(load_profile < 0)[0])

    # check msg
    if len(infeas_list) > 0:
        check_msg["bounds"] = "nok"

    # calculate and return infeasibility score
    return check_msg, calculate_infeas_score(n_infeas_check=n_infeas_check, infeas_list=infeas_list,
                                             n_default_infeas=n_default_infeas)


def check_charging_station_feasibility(charging_station_env: ChargingStationEnv, load_profiles: np.ndarray,
                                       t_ev_dep: np.ndarray, t_ev_arr: np.ndarray, n_ts: int, delta_t_s: int,
                                       dep_soc_penalty: float) -> (float, float):
    """
    Check EV load profiles obtained from the charging station module

    :param charging_station_env: ChargingStationEnv object, from which the main params necessary for this
    check can be obtained
    :param load_profiles: matrix with a line per EV charging profile.
    :param t_ev_dep: time-slots of dep.
    :param t_ev_arr: idem for arr (after dep. here, back from work)
    :param n_ts: number of time-slots
    :param delta_t_s: time-slot duration, in seconds
    :param dep_soc_penalty: value of the penalty to be added to the objective if EV SoC at departure is below 25% of
    battery capa
    :return: returns the obj. penalty (for not being charged at a minimum SOC of 4kWh at dep.) and the
    infeasibility score
    """
    # get a few params
    n_ev = len(charging_station_env.evs)
    agent_type = "charging_station"
    type_and_size_check = check_load_profile_type_and_size(agent_type=agent_type, load_profile=load_profiles,
                                                           n_ts=n_ts, n_ev=n_ev)
    if any([check_status is False for check_status in type_and_size_check.values()]):
        print(msg_error_type_and_size(agent_type=agent_type, n_ts=n_ts))
        return None, WRONG_FORMAT_SCORE, {}

    infeas_list = []
    detailed_infeas_list = []
    n_default_infeas = 0
    n_infeas_by_type = {"ev_max_p": 0, "ev_min_p": 0, "charge_out_of_cs": 0, "soc_max_bound": 0,
                        "soc_min_bound": 0, "min_soc_at_dep": 0, "cs_max_power": 0}
    n_infeas_check = 0  # number of constraints checked (to normalize the infeas. score at the end)

    # check
    # 1. that indiv. charging powers respect the indiv. max. and min. power limits
    for i_ev in range(n_ev):
        # max power
        pmax = charging_station_env.evs[i_ev].battery.pmax
        indiv_max_power_check = list(np.maximum(load_profiles[i_ev, :] - pmax, 0) / pmax)
        infeas_list.extend(indiv_max_power_check)
        n_infeas_check += n_ts
        # update infeas by type
        n_infeas_by_type["ev_max_p"] += len(np.where(np.array(infeas_list[-n_ts:]) > 0)[0])
        # and store detailed infeasibilities
        detailed_infeas_list.extend([f"ev{i_ev}_indiv_max_p_t{t}"
                                     for t in range(n_ts) if indiv_max_power_check[t] > 0])
        # and min power
        pmin = charging_station_env.evs[i_ev].battery.pmin
        indiv_min_power_check = list(np.maximum(pmin - load_profiles[i_ev, :], 0) / pmin)
        infeas_list.extend(indiv_min_power_check)
        n_infeas_check += n_ts
        # update infeas by type
        n_infeas_by_type["ev_min_p"] += len(np.where(np.array(infeas_list[-n_ts:]) > 0)[0])
        # and store detailed infeasibilities
        detailed_infeas_list.extend([f"ev{i_ev}_indiv_min_p_t{t}"
                                     for t in range(n_ts) if indiv_min_power_check[t] > 0])

    # 0 charging power when EV is not connected (convention that EV leave at the end
    # of time-slot t_dep and arrive at the beginning of t_arr -> can charge in both ts)
    for i_ev in range(n_ev):
        # TODO OJ: get directly t_ev_dep and t_ev_arr from ev.data?
        charge_when_plugged_check = np.where(np.abs(load_profiles[i_ev, t_ev_dep[i_ev]+1:t_ev_arr[i_ev]-1]) > 0)[0]
        n_charge_out_of_cs = len(charge_when_plugged_check)
        n_default_infeas += n_charge_out_of_cs
        # update infeas by type
        n_infeas_by_type["charge_out_of_cs"] += n_charge_out_of_cs
        # and store detailed infeasibilities
        detailed_infeas_list.extend([f"ev{i_ev}_charge_when_not_plugged_t{t}" for t in charge_when_plugged_check])

    # 2. that SoC bounds of each EV is respected, as well as min. charging need at dep.
    cs_dep_soc_penalty = 0
    for i_ev in range(n_ev):
        # get a few params from charging station env.
        ev_init_soc = charging_station_env.evs[i_ev].battery.initial_soc
        ev_batt_capa = charging_station_env.evs[i_ev].battery.capacity
        charge_eff = charging_station_env.evs[i_ev].battery.efficiency
        current_batt_soc = \
            calculate_ev_soc_trajectory(ev_init_soc=ev_init_soc, load_profile=load_profiles[i_ev, :],
                                        charge_eff=charge_eff, discharge_eff=charge_eff,
                                        ev_arrival_time=t_ev_arr[i_ev], delta_t_s=delta_t_s)

        # max bound (EV batt. capa)
        max_soc_check = list(np.maximum(current_batt_soc - ev_batt_capa, 0) / ev_batt_capa)
        infeas_list.extend(max_soc_check)
        n_infeas_check += n_ts
        n_infeas_by_type["soc_max_bound"] += len(np.where(np.array(infeas_list[-n_ts:]) > 0)[0])
        # and store detailed infeasibilities
        detailed_infeas_list.extend([f"ev{i_ev}_max_soc_t{t}" for t in range(n_ts) if max_soc_check[t] > 0])

        # min bound (0)
        min_soc_check = np.where(current_batt_soc < 0)[0]
        n_soc_below_zero = len(min_soc_check)
        n_default_infeas += n_soc_below_zero
        n_infeas_by_type["soc_min_bound"] += n_soc_below_zero
        # and store detailed infeasibilities
        detailed_infeas_list.extend([f"ev{i_ev}_min_soc_t{t}" for t in min_soc_check])

        # SoC at dep. is above the minimal level requested
        if current_batt_soc[t_ev_dep[i_ev]] < 0.25 * ev_batt_capa:
            cs_dep_soc_penalty += dep_soc_penalty
            n_infeas_by_type["min_soc_at_dep"] += 1
            detailed_infeas_list.append(f"ev{i_ev}_min_soc_at_dep_t{t_ev_dep[i_ev]}")
        n_infeas_check += 1

    # 3.that CS power is below the max allowed value
    cs_max_power = charging_station_env.pmax_site
    charging_station_max_power_check = list(np.maximum(np.abs(np.sum(load_profiles, axis=0))
                                                       - cs_max_power, 0) / cs_max_power)
    infeas_list.extend(charging_station_max_power_check)
    n_infeas_check += n_ts
    n_infeas_by_type["cs_max_power"] += len(np.where(np.array(infeas_list[-n_ts:]) > 0)[0])
    # and store detailed infeasibilities
    detailed_infeas_list.extend([f"station_max_p_t{t}" for t in range(n_ts)
                                 if charging_station_max_power_check[t] > 0])

    # calculate infeasibility score
    infeas_score = calculate_infeas_score(n_infeas_check, infeas_list, n_default_infeas)

    return cs_dep_soc_penalty, infeas_score, n_infeas_by_type, detailed_infeas_list


def check_solar_farm_feasibility(load_profile: np.ndarray, batt_capa: float, batt_max_power: float, charge_eff: float,
                                 discharge_eff: float, n_ts: int, delta_t_s: int) -> float:
    """
    Check battery load profile obtained from the Solar Farm module. Note: idem
    Industrial Site feas. check in the current version of the modelling
    
    :param load_profile: vector with battery load
    :param batt_capa: battery capacity
    :param batt_max_power: batt max (charge and discharge) power
    :param charge_eff: charging efficiency
    :param discharge_eff: discharging efficiency
    :param n_ts: number of time-slots
    :param delta_t_s: time-slot duration, in seconds
    :return: returns the infeasibility score
    """

    agent_type = "solar_farm"
    type_and_size_check = \
        check_load_profile_type_and_size(agent_type=agent_type, load_profile=load_profile, n_ts=n_ts)
    if any([check_status is False for check_status in type_and_size_check.values()]):
        print(msg_error_type_and_size(agent_type=agent_type, n_ts=n_ts))
        return WRONG_FORMAT_SCORE, {}

    return check_industrial_cons_feasibility(load_profile, batt_capa, batt_max_power, charge_eff, discharge_eff,
                                             n_ts, delta_t_s)


def check_industrial_cons_feasibility(load_profile: np.ndarray, battery: Battery, n_ts: int, delta_t_s: int) -> float:
    """
    Check battery load profile obtained from the Industrial Cons. module
    
    :param load_profile: vector with battery load
    :param battery: the Battery object on industrial site
    :param n_ts: number of time-slots
    :param delta_t_s: time-slot duration, in seconds
    :return: returns the infeasibility score
    """
    type_and_size_check = check_load_profile_type_and_size(agent_type="industrial_consumer",
                                                           load_profile=load_profile, n_ts=n_ts)

    if not (isinstance(load_profile, np.ndarray) and load_profile.shape == (n_ts,)):
        print("Wrong format for Industrial Site load profile, should be (%i,)" % n_ts)
        
        return WRONG_FORMAT_SCORE, {}
    
    infeas_list = []
    n_default_infeas = 0
    n_infeas_by_type = {"batt_max_p": 0, "soc_max_bound": 0, "soc_min_bound": 0}
    n_infeas_check = 0  # number of constraints checked (to normalize the infeas. score at the end)

    # check
    # 1. that battery charging powers respect the max. power limit
    infeas_list.extend(list(np.maximum(np.abs(load_profile) - battery.pmax, 0) / battery.pmax))
    n_infeas_check += n_ts
    # update infeas by type
    n_infeas_by_type["batt_max_p"] += len(np.where(np.array(infeas_list[-n_ts:]) > 0)[0])

    # 2. that batt. SoC bounds are respected
    batt_soc = (battery.efficiency * np.cumsum(np.maximum(load_profile, 0)) \
                        - battery.efficiency * np.cumsum(np.maximum(-load_profile, 0))) \
                                    * delta_t_s / 3600
    # max bound (batt. capa)
    infeas_list.extend(list(np.maximum(batt_soc - battery.capacity, 0) / battery.capacity))
    n_infeas_check += n_ts
    n_infeas_by_type["soc_max_bound"] += len(np.where(np.array(infeas_list[-n_ts:]) > 0)[0])
    # min bound (0)
    n_soc_below_zero = len(np.where(batt_soc < 0)[0])
    n_default_infeas += n_soc_below_zero
    n_infeas_by_type["soc_min_bound"] += n_soc_below_zero

    # calculate infeasibility score
    infeas_score = calculate_infeas_score(n_infeas_check, infeas_list, n_default_infeas)

    return infeas_score, n_infeas_by_type


if __name__ == "__main__":
    # Create and test agents load profiles
    delta_t_s = 1800
    n_ts = 48  # nber of time-slots
    random_load_prof = np.random.rand(n_ts)
    # # DATA CENTER
    # data_center = DataCenter()
    # print("Test DATACENTER profile feasibility")
    # dc_infeas_score = \
    #     check_data_center_feasibility(data_center=data_center, load_profile=random_load_prof,
    #                                   it_load_profile=np.random.rand(n_ts), n_ts=n_ts, delta_t_s=delta_t_s)
    # print(f"DC infeas score: {dc_infeas_score}")
    # # EV CHARGING STATION
    # from microgrid.config import get_configs
    # seed = 1234
    # configs = get_configs(seed)
    # charging_station_env = ChargingStationEnv(station_config=configs['station_config'], nb_pdt=48)
    # n_ev = 4
    # ev_load_profiles = np.random.rand(n_ev, n_ts)
    # t_ev_dep = np.array([25, 23, 28, 30])
    # t_ev_arr = np.array([2, 3, 1, 5])
    # print("Test EV profiles feasibility")
    # cs_infeas_score = check_charging_station_feasibility(charging_station_env=charging_station_env,
    #                                                      load_profiles=ev_load_profiles, t_ev_dep=t_ev_dep,
    #                                                      t_ev_arr=t_ev_arr, n_ts=n_ts, delta_t_s=delta_t_s,
    #                                                      dep_soc_penalty=1000)
    # print(f"CS infeas score: {cs_infeas_score}")
    # #
    # # load_profiles = np.zeros((4, n_ts))
    # # # 1st totally random
    # # load_profiles[0, :] = 1.5 * ev_max_powers["normal"] * np.random.rand(n_ts)
    # # # 2nd taking into account plug-in period
    # # load_profiles[1, t_ev_arr[1]:t_ev_dep[1]] = ev_max_powers["normal"] * np.random.rand(t_ev_dep[1] - t_ev_arr[1])
    # # # 3rd and 4th taking into account both plug-in period and charging need
    # # for i_ev in range(2, 4):
    # #     good_profile = ev_max_powers["fast"] * np.random.rand(t_ev_dep[i_ev] - t_ev_arr[i_ev])
    # #     good_profile *= 0.25 * ev_batt_capa[i_ev] * 3600 / delta_t_s / sum(good_profile)
    # #     load_profiles[i_ev, t_ev_arr[i_ev]:t_ev_dep[i_ev]] = good_profile
    # #
    # # cs_dep_soc_penalty, infeas_score, n_infeas_by_type, detailed_infeas_list = \
    # # check_charging_station_feasibility(load_profiles=load_profiles, n_ev_normal_charging=n_ev_normal_charging,
    # #                                    n_ev_fast_charging=n_ev_fast_charging, t_ev_dep=t_ev_dep, t_ev_arr=t_ev_arr,
    # #                                    ev_max_powers=ev_max_powers, ev_batt_capa=ev_batt_capa, ev_init_soc=ev_init_soc,
    # #                                    charge_eff=charge_eff, discharge_eff=discharge_eff, n_ts=n_ts,
    # #                                    delta_t_s=delta_t_s, dep_soc_penalty=dep_soc_penalty, cs_max_power=cs_max_power)
    # # print("number of infeasibilities per type", n_infeas_by_type)
    # # print("Detailed infeasibilities:", detailed_infeas_list)
    # #