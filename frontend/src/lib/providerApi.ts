// The provider API each execution step stands for. The backend sandbox computes the same names
// (backend/countersign/execution/sandbox.py, API) but the execution_step event and the action outcome do
// not carry them yet, so the UI derives them from the action's provider and the step name.

const API: Record<string, string> = {
  "aws:stop": "ec2:StopInstances",
  "aws:start": "ec2:StartInstances",
  "aws:resize": "ec2:ModifyInstanceAttribute (InstanceType)",
  "aws:resize_back": "ec2:ModifyInstanceAttribute (InstanceType)",
  "aws:snapshot": "ec2:CreateSnapshot",
  "aws:delete": "ec2:DeleteVolume",
  "aws:release": "ec2:ReleaseAddress",
  "azure:stop": "Microsoft.Compute/virtualMachines/deallocate",
  "azure:start": "Microsoft.Compute/virtualMachines/start",
  "azure:resize": "Microsoft.Compute/virtualMachines/write (hardwareProfile.vmSize)",
  "azure:resize_back": "Microsoft.Compute/virtualMachines/write (hardwareProfile.vmSize)",
  "azure:snapshot": "Microsoft.Compute/snapshots/write",
  "azure:delete": "Microsoft.Compute/disks/delete",
  "azure:release": "Microsoft.Network/publicIPAddresses/delete",
};

export function providerApi(provider: string | undefined, step: string, explicit?: unknown): string {
  if (typeof explicit === "string" && explicit) return explicit;
  return API[`${provider ?? ""}:${step}`] ?? step;
}
