class DynamoDbFailedJobProvider:
    def extend(self, other):
        return self.all(other)

    def all(self, other):
        cursor = self.conn()
        return cursor.execute("scan " + other)

    def conn(self):
        return object()
