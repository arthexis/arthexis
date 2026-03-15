with Ada.Strings.Unbounded;

package Arthexis.ORM is
   """GNATCOLL-backed ORM bootstrap and SQL registration helpers."""

   type Database_Connection is tagged private;

   function Connect_SQLite (Path : String) return Database_Connection;
   --  Open a SQLite connection through GNATCOLL.SQL.Sqlite.

   procedure Execute (Conn : in out Database_Connection; SQL : String);
   --  Execute arbitrary SQL on the active connection.

   procedure Register_SQL_Function
     (Conn      : in out Database_Connection;
      Name      : String;
      Arity     : Natural;
      SQL_Body  : String);
   --  Register a SQL-callable function backed by app-owned SQL text.

   procedure Register_Trigger
     (Conn      : in out Database_Connection;
      Name      : String;
      SQL_Body  : String);
   --  Register a SQL trigger from an app trigger definition.

private
   use Ada.Strings.Unbounded;

   type Database_Connection is tagged record
      URI : Unbounded_String;
   end record;
end Arthexis.ORM;
