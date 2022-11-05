# Copyright 2022 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Module containing Paranoid single checks for EC keys."""

from absl import logging
from paranoid_crypto import paranoid_pb2
from paranoid_crypto.lib import base_check
from paranoid_crypto.lib import consts
from paranoid_crypto.lib import ec_util
from paranoid_crypto.lib import util


class CheckValidECKey(base_check.ECKeyCheck):
  """Checks whether the public keys are valid.

  The key is considered valid if:
  (1) the curve is known
  (2) the point is on the curve
  (3) if the cofactor h of the curve is larger than 1 then the test
      checks that the point is in the subgroup generated by generator of the
      curve.

  Seeing an invalid EC key might indicate that the creator of the key is
  attempting to perform an invalid curve attack. Therefore, if invalid EC keys
  are discovered then one should investigate the keys and the application
  using these keys.
  """

  def __init__(self):
    super().__init__(paranoid_pb2.SeverityType.SEVERITY_MEDIUM)

  def Check(self, artifacts: list[paranoid_pb2.ECKey]) -> bool:
    any_weak = False
    for key in artifacts:
      test_result = self._CreateTestResult()
      curve = ec_util.CURVE_FACTORY.get(key.ec_info.curve_type, None)
      if curve is None:
        logging.warning("Unknown curve: %s", key.ec_info)
        any_weak = True
        test_result.result = True
      else:
        if not curve.IsValidPublicKey(ec_util.PublicPoint(key.ec_info)):
          logging.warning("Invalid public key: %s", key.ec_info)
          any_weak = True
          test_result.result = True
      util.SetTestResult(key.test_info, test_result)
    return any_weak


class CheckWeakCurve(base_check.ECKeyCheck):
  """Checks whether weak curves are used.

  A curve is considered weak if discrete logarithms take significantly
  less than 2**112 operations.
  """

  def __init__(self):
    super().__init__(paranoid_pb2.SeverityType.SEVERITY_MEDIUM)

  def Check(self, artifacts: list[paranoid_pb2.ECKey]) -> bool:
    any_weak = False
    # The minimal bit_length of the order of the curve. The order of a 224-bit
    # curve can be slightly smaller than 2**224. Such curves would not fail the
    # test since (2**223).bit_length == 224.
    minimal_bit_length = 224
    for key in artifacts:
      curve = ec_util.CURVE_FACTORY.get(key.ec_info.curve_type, None)
      if curve is None:
        # Skipping the test. CheckValidECKey already checks this.
        continue
      test_result = self._CreateTestResult()
      if curve.n.bit_length() < minimal_bit_length:
        logging.warning("Weak curve: %s", key.ec_info)
        any_weak = True
        test_result.result = True
      util.SetTestResult(key.test_info, test_result)
    return any_weak


class CheckWeakECPrivateKey(base_check.ECKeyCheck):
  """Checks if any keys use a weak private key.

  This method checks whether there are any keys where the private key is
  of one of the following forms:
    (1) only the 32 least significant bits are set.
    (2) only the 32 most significant bits are set.
    (3) all 32 bit words of the private key are the same.

  The test is motivated by some research that found such weak keys
  https://www.securityevaluators.com/casestudies/ethercombing/

  An observation used in the method below is that it is sufficient to
  solve DLs for the 32 least significant bits. A point of form (2) can be
  converted into a point of form (1) by a point multiplication with
  2^(-curve.bit_length() + 32) modulo the order of the curve.
  A point of form (3) can be converted into a point of form (1) by
  multiplying it with the modular inverse of (1 + 2^32 + 2^64 + ...).

  The method uses two steps. Step 1 generated a list of points with all
  the conversions. Step 2 performs a baby step giant step algorithm with
  all the generated points.

  The time and memory complexity of the methods is
     O(sqrt(#forms * #keys * 2^32))
  """

  def __init__(self):
    super().__init__(paranoid_pb2.SeverityType.SEVERITY_CRITICAL)

  def Check(self, artifacts: list[paranoid_pb2.ECKey]) -> bool:
    any_weak = False
    for curve_id, curve in ec_util.CURVE_FACTORY.items():
      if curve is None:
        continue
      # Generate a batch of keys using the same curve, since this check is
      # significantly faster when keys are checked in batches, rather than
      # individually.
      keys = [key for key in artifacts if key.ec_info.curve_type == curve_id]
      if not keys:
        continue
      points = [ec_util.PublicPoint(key.ec_info) for key in keys]
      discrete_logs = curve.ExtendedBatchDL(points)
      for i, key in enumerate(keys):
        test_result = self._CreateTestResult()
        if discrete_logs[i] is not None:
          discrete_log = format(int(discrete_logs[i]), "x")
          logging.warning("Discrete logarithm found: %s %s", discrete_log,
                          key.ec_info)
          util.AttachInfo(key.test_info, consts.INFO_NAME_DISCRETE_LOG,
                          discrete_log)
          any_weak = True
          test_result.result = True
        util.SetTestResult(key.test_info, test_result)
    return any_weak
