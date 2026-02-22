class FakeContainer:
    def __init__(self, name, status="running"):
        self.name = name
        self.status = status

    def restart(self, timeout=30):
        self.status = "running"


class FakeContainerCollection:
    def __init__(self):
        self._containers = {"vault-vllm": FakeContainer("vault-vllm")}

    def get(self, name):
        if name in self._containers:
            return self._containers[name]
        raise Exception(f"Container {name} not found")


class FakeDockerClient:
    def __init__(self):
        self.containers = FakeContainerCollection()
